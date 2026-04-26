"""
IceChaser Calibration Engine

Replays historical NHL seasons, runs our Monte Carlo model at various
points in the season, and compares predicted probabilities to actual
playoff outcomes. Produces calibration metrics.

Tests: Did teams we gave X% actually make the playoffs X% of the time?
"""

import json
import os
import sys
import time
import urllib.request
import numpy as np
from collections import defaultdict
from datetime import datetime, timedelta

# Import our sim engine
import simulator_np as sim
import elo_engine

# Historical playoff teams (teams that actually made the playoffs)
# Source: NHL records
PLAYOFF_TEAMS = {
    20242025: [
        # Eastern: ATL div top 3, Metro top 3, WC1, WC2
        "TOR", "TBL", "FLA", "WSH", "CAR", "NJD", "BOS", "OTT",
        # Western: Central top 3, Pacific top 3, WC1, WC2
        "WPG", "DAL", "COL", "VGK", "EDM", "LAK", "MIN", "STL",
    ],
    20232024: [
        # Eastern
        "NYR", "CAR", "FLA", "BOS", "TOR", "TBL", "NYI", "WSH",
        # Western
        "DAL", "WPG", "COL", "VAN", "EDM", "NSH", "LAK", "VGK",
    ],
    20222023: [
        # Eastern
        "BOS", "CAR", "NJD", "TOR", "NYR", "FLA", "TBL", "NYI",
        # Western
        "VGK", "EDM", "COL", "DAL", "MIN", "WPG", "SEA", "LAK",
    ],
}

SEASON_STARTS = {
    20242025: "2024-10-08",
    20232024: "2023-10-10",
    20222023: "2022-10-07",
}

# How many teams played each season
TEAMS_PER_SEASON = 32


def fetch_season_games(season_start, season_id):
    """Fetch all completed regular season games for a historical season."""
    all_games = []
    current_date = season_start
    seen_ids = set()
    
    # Regular season ends mid-April
    year = int(season_start[:4])
    end_date = f"{year + 1}-04-20"
    
    print(f"  📡 Fetching games from {season_start} to {end_date}...")
    
    while current_date and current_date <= end_date:
        try:
            url = f"https://api-web.nhle.com/v1/schedule/{current_date}"
            req = urllib.request.Request(url, headers={"User-Agent": "IceChaser-Calibration/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            print(f"    ⚠ Error fetching {current_date}: {e}")
            # Try next week
            d = datetime.strptime(current_date, "%Y-%m-%d")
            current_date = (d + timedelta(days=7)).strftime("%Y-%m-%d")
            continue
        
        next_date = data.get("nextStartDate")
        
        for day in data.get("gameWeek", []):
            for game in day.get("games", []):
                state = game.get("gameState", "")
                if state not in ("OFF", "FINAL"):
                    continue
                if game.get("gameType") != 2:  # Regular season only
                    continue
                if game["id"] in seen_ids:
                    continue
                
                home = game.get("homeTeam", {})
                away = game.get("awayTeam", {})
                outcome = game.get("gameOutcome", {})
                
                seen_ids.add(game["id"])
                all_games.append({
                    "id": game["id"],
                    "date": day["date"],
                    "home": home.get("abbrev", ""),
                    "away": away.get("abbrev", ""),
                    "home_score": home.get("score", 0),
                    "away_score": away.get("score", 0),
                    "overtime": outcome.get("lastPeriodType", "REG") in ("OT", "SO"),
                    "home_win": home.get("score", 0) > away.get("score", 0),
                })
        
        if not next_date or next_date <= current_date:
            break
        current_date = next_date
        time.sleep(0.05)
    
    all_games.sort(key=lambda g: (g["date"], g["id"]))
    print(f"    ✓ {len(all_games)} games fetched")
    return all_games


def build_standings_at_game(games_played, all_teams):
    """
    Build standings from a list of played games.
    Returns a list of team dicts compatible with our simulator.
    """
    # Initialize team stats
    stats = {}
    for abbrev in all_teams:
        stats[abbrev] = {
            "teamAbbrev": abbrev,
            "points": 0,
            "wins": 0,
            "losses": 0,
            "otLosses": 0,
            "regulationWins": 0,
            "gamesPlayed": 0,
            "gamesRemaining": 82,
        }
    
    for game in games_played:
        h, a = game["home"], game["away"]
        if h not in stats or a not in stats:
            continue
        
        stats[h]["gamesPlayed"] += 1
        stats[a]["gamesPlayed"] += 1
        
        if game["home_win"]:
            stats[h]["wins"] += 1
            stats[h]["points"] += 2
            if not game["overtime"]:
                stats[h]["regulationWins"] += 1
                stats[a]["losses"] += 1
            else:
                stats[a]["otLosses"] += 1
                stats[a]["points"] += 1
        else:
            stats[a]["wins"] += 1
            stats[a]["points"] += 2
            if not game["overtime"]:
                stats[a]["regulationWins"] += 1
                stats[h]["losses"] += 1
            else:
                stats[h]["otLosses"] += 1
                stats[h]["points"] += 1
    
    # Calculate derived fields
    teams = []
    for abbrev, s in stats.items():
        gp = s["gamesPlayed"]
        s["gamesRemaining"] = 82 - gp
        if gp > 0:
            s["pointsPace"] = round(s["points"] / gp * 82, 1)
        else:
            s["pointsPace"] = 82  # Default to ~1 pt/game
        teams.append(s)
    
    return teams


def build_remaining_schedule(all_games, games_played_count):
    """
    Build list of remaining games as (home, away) tuples.
    """
    played = all_games[:games_played_count]
    remaining = all_games[games_played_count:]
    return [(g["home"], g["away"]) for g in remaining]


def assign_conferences_divisions(teams, season_games):
    """
    Infer conference/division from the NHL API standings for a season.
    Since we're replaying, hardcode the standard 32-team structure.
    """
    # Standard NHL conference/division assignments (stable since 2017-18 realignment,
    # with minor changes for Utah/Arizona)
    CONF_DIV = {
        # Eastern - Atlantic
        "BOS": ("Eastern", "Atlantic"), "BUF": ("Eastern", "Atlantic"),
        "DET": ("Eastern", "Atlantic"), "FLA": ("Eastern", "Atlantic"),
        "MTL": ("Eastern", "Atlantic"), "OTT": ("Eastern", "Atlantic"),
        "TBL": ("Eastern", "Atlantic"), "TOR": ("Eastern", "Atlantic"),
        # Eastern - Metropolitan
        "CAR": ("Eastern", "Metropolitan"), "CBJ": ("Eastern", "Metropolitan"),
        "NJD": ("Eastern", "Metropolitan"), "NYI": ("Eastern", "Metropolitan"),
        "NYR": ("Eastern", "Metropolitan"), "PHI": ("Eastern", "Metropolitan"),
        "PIT": ("Eastern", "Metropolitan"), "WSH": ("Eastern", "Metropolitan"),
        # Western - Central
        "ARI": ("Western", "Central"), "UTA": ("Western", "Central"),
        "CHI": ("Western", "Central"), "COL": ("Western", "Central"),
        "DAL": ("Western", "Central"), "MIN": ("Western", "Central"),
        "NSH": ("Western", "Central"), "STL": ("Western", "Central"),
        "WPG": ("Western", "Central"),
        # Western - Pacific
        "ANA": ("Western", "Pacific"), "CGY": ("Western", "Pacific"),
        "EDM": ("Western", "Pacific"), "LAK": ("Western", "Pacific"),
        "SJS": ("Western", "Pacific"), "SEA": ("Western", "Pacific"),
        "VAN": ("Western", "Pacific"), "VGK": ("Western", "Pacific"),
    }
    
    for t in teams:
        abbrev = t["teamAbbrev"]
        if abbrev in CONF_DIV:
            t["conference"], t["division"] = CONF_DIV[abbrev]
        else:
            # Fallback
            t["conference"] = "Eastern"
            t["division"] = "Atlantic"
    
    return teams


def run_calibration_point(teams, remaining_schedule, elo_ratings, actual_playoff_teams, label=""):
    """
    Run our model at one point in a season.
    Returns dict of {abbrev: predicted_playoff_pct}.
    """
    # Clear Elo cache and set ratings
    sim._elo_ratings_cache = elo_ratings
    
    # Run 100k sim
    try:
        results = sim.run_simulations_np(teams, num_simulations=100000, real_schedule=remaining_schedule)
        preds = {}
        for abbrev in results:
            preds[abbrev] = results[abbrev]["playoff_pct"]
        return preds
    except Exception as e:
        print(f"    ⚠ Sim error at {label}: {e}")
        import traceback
        traceback.print_exc()
        return {}


def run_full_calibration():
    """
    Run calibration across multiple seasons and checkpoints.
    """
    # Calibration checkpoints: games remaining per team (approximate)
    checkpoints = [30, 20, 15, 10, 5]
    
    # Collect all predictions grouped by probability bucket
    # bucket -> [list of (predicted_pct, actually_made_playoffs)]
    all_predictions = []
    
    results_by_season = {}
    
    for season_id, season_start in sorted(SEASON_STARTS.items()):
        season_label = f"{str(season_id)[:4]}-{str(season_id)[4:]}"
        print(f"\n{'='*60}")
        print(f"📅 Season {season_label}")
        print(f"{'='*60}")
        
        # Fetch all games
        cache_file = f"/tmp/nhl_games_{season_id}.json"
        if os.path.exists(cache_file):
            print(f"  📂 Loading cached games...")
            with open(cache_file) as f:
                all_games = json.load(f)
            print(f"    ✓ {len(all_games)} games loaded from cache")
        else:
            all_games = fetch_season_games(season_start, season_id)
            with open(cache_file, "w") as f:
                json.dump(all_games, f)
        
        if not all_games:
            print(f"  ⚠ No games found — skipping")
            continue
        
        # Get all team abbreviations that played
        all_team_abbrevs = set()
        for g in all_games:
            all_team_abbrevs.add(g["home"])
            all_team_abbrevs.add(g["away"])
        
        actual_playoff = set(PLAYOFF_TEAMS.get(season_id, []))
        
        # Build Elo ratings for this season
        print(f"  🧮 Computing Elo ratings for full season...")
        full_elo, _ = elo_engine.compute_elo_ratings(all_games)
        
        total_games = len(all_games)
        avg_games_per_team = total_games * 2 / len(all_team_abbrevs)  # each game involves 2 teams
        
        season_results = {}
        
        for games_remaining_target in checkpoints:
            # Find the game index where avg games remaining ≈ target
            target_gp = 82 - games_remaining_target
            # Approximate: game_index ≈ target_gp / 82 * total_games
            game_idx = int(target_gp / 82 * total_games)
            game_idx = min(game_idx, total_games - 1)
            
            games_played = all_games[:game_idx]
            remaining = all_games[game_idx:]
            
            if not games_played:
                continue
            
            # Build standings at this point
            teams = build_standings_at_game(games_played, all_team_abbrevs)
            teams = assign_conferences_divisions(teams, all_games)
            
            avg_gp = np.mean([t["gamesPlayed"] for t in teams])
            avg_gr = np.mean([t["gamesRemaining"] for t in teams])
            
            # Build Elo up to this point
            elo_at_point, _ = elo_engine.compute_elo_ratings(games_played)
            
            remaining_schedule = [(g["home"], g["away"]) for g in remaining]
            
            label = f"~{games_remaining_target} games left (avg GP: {avg_gp:.0f})"
            print(f"\n  📍 Checkpoint: {label}")
            
            preds = run_calibration_point(
                teams, remaining_schedule, elo_at_point, actual_playoff, label
            )
            
            if not preds:
                continue
            
            # Record predictions
            for abbrev, pred_pct in preds.items():
                actually_made = abbrev in actual_playoff
                all_predictions.append({
                    "season": season_label,
                    "games_remaining": games_remaining_target,
                    "team": abbrev,
                    "predicted_pct": pred_pct,
                    "made_playoffs": actually_made,
                })
            
            # Print notable predictions
            sorted_preds = sorted(preds.items(), key=lambda x: -x[1])
            print(f"    Top bubble teams:")
            for abbrev, pct in sorted_preds:
                if 5 < pct < 95:
                    made = "✓" if abbrev in actual_playoff else "✗"
                    print(f"      {abbrev}: {pct:.1f}% {made}")
            
            season_results[games_remaining_target] = preds
        
        results_by_season[season_label] = season_results
    
    # ========================================
    # Calibration Analysis
    # ========================================
    print(f"\n\n{'='*60}")
    print(f"📊 CALIBRATION RESULTS")
    print(f"{'='*60}")
    
    if not all_predictions:
        print("No predictions to analyze!")
        return
    
    # Bucket predictions by predicted probability
    buckets = [
        (0, 5, "0-5%"),
        (5, 15, "5-15%"),
        (15, 25, "15-25%"),
        (25, 35, "25-35%"),
        (35, 45, "35-45%"),
        (45, 55, "45-55%"),
        (55, 65, "55-65%"),
        (65, 75, "65-75%"),
        (75, 85, "75-85%"),
        (85, 95, "85-95%"),
        (95, 100.1, "95-100%"),
    ]
    
    print(f"\n{'Bucket':>12}  {'Predicted':>10}  {'Actual':>10}  {'Count':>6}  {'Delta':>8}")
    print("-" * 55)
    
    total_brier = 0.0
    total_count = 0
    
    for lo, hi, label in buckets:
        in_bucket = [p for p in all_predictions if lo <= p["predicted_pct"] < hi]
        if not in_bucket:
            continue
        
        avg_pred = np.mean([p["predicted_pct"] for p in in_bucket])
        avg_actual = np.mean([1.0 if p["made_playoffs"] else 0.0 for p in in_bucket]) * 100
        count = len(in_bucket)
        delta = avg_actual - avg_pred
        
        # Brier score contribution
        for p in in_bucket:
            pred_frac = p["predicted_pct"] / 100.0
            actual = 1.0 if p["made_playoffs"] else 0.0
            total_brier += (pred_frac - actual) ** 2
            total_count += 1
        
        marker = ""
        if abs(delta) > 10:
            marker = " ⚠"
        
        print(f"{label:>12}  {avg_pred:>9.1f}%  {avg_actual:>9.1f}%  {count:>6}  {delta:>+7.1f}%{marker}")
    
    brier = total_brier / total_count if total_count > 0 else 0
    print(f"\nBrier Score: {brier:.4f} (lower is better; 0.25 = random, 0.0 = perfect)")
    print(f"Total predictions: {total_count}")
    
    # By games remaining
    print(f"\n📈 Calibration by Games Remaining:")
    for gr in sorted(set(p["games_remaining"] for p in all_predictions)):
        subset = [p for p in all_predictions if p["games_remaining"] == gr]
        brier_gr = np.mean([(p["predicted_pct"]/100 - (1 if p["made_playoffs"] else 0))**2 for p in subset])
        bubble = [p for p in subset if 10 < p["predicted_pct"] < 90]
        bubble_brier = np.mean([(p["predicted_pct"]/100 - (1 if p["made_playoffs"] else 0))**2 for p in bubble]) if bubble else 0
        print(f"  ~{gr:>2} games left: Brier={brier_gr:.4f} (all), Brier={bubble_brier:.4f} (bubble 10-90%), n={len(subset)}")
    
    # Save full results
    output = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "seasons_tested": list(SEASON_STARTS.keys()),
        "checkpoints": checkpoints,
        "total_predictions": total_count,
        "brier_score": round(brier, 4),
        "predictions": all_predictions,
    }
    
    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "calibration_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n💾 Full results saved to {out_path}")


if __name__ == "__main__":
    run_full_calibration()
