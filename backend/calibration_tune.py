"""
Grid search over K-factor, home bonus, and OT discount to minimize Brier score.
Uses cached game data from the full calibration run.
"""

import json
import os
import sys
import numpy as np
from collections import defaultdict

import simulator_np as sim
import elo_engine

# Load cached games
SEASONS = [20222023, 20232024, 20242025]
CHECKPOINTS = [30, 20, 15, 10, 5]

PLAYOFF_TEAMS = {
    20242025: {"TOR", "TBL", "FLA", "WSH", "CAR", "NJD", "BOS", "OTT",
               "WPG", "DAL", "COL", "VGK", "EDM", "LAK", "MIN", "STL"},
    20232024: {"NYR", "CAR", "FLA", "BOS", "TOR", "TBL", "NYI", "WSH",
               "DAL", "WPG", "COL", "VAN", "EDM", "NSH", "LAK", "VGK"},
    20222023: {"BOS", "CAR", "NJD", "TOR", "NYR", "FLA", "TBL", "NYI",
               "VGK", "EDM", "COL", "DAL", "MIN", "WPG", "SEA", "LAK"},
}

CONF_DIV = {
    "BOS": ("Eastern", "Atlantic"), "BUF": ("Eastern", "Atlantic"),
    "DET": ("Eastern", "Atlantic"), "FLA": ("Eastern", "Atlantic"),
    "MTL": ("Eastern", "Atlantic"), "OTT": ("Eastern", "Atlantic"),
    "TBL": ("Eastern", "Atlantic"), "TOR": ("Eastern", "Atlantic"),
    "CAR": ("Eastern", "Metropolitan"), "CBJ": ("Eastern", "Metropolitan"),
    "NJD": ("Eastern", "Metropolitan"), "NYI": ("Eastern", "Metropolitan"),
    "NYR": ("Eastern", "Metropolitan"), "PHI": ("Eastern", "Metropolitan"),
    "PIT": ("Eastern", "Metropolitan"), "WSH": ("Eastern", "Metropolitan"),
    "ARI": ("Western", "Central"), "UTA": ("Western", "Central"),
    "CHI": ("Western", "Central"), "COL": ("Western", "Central"),
    "DAL": ("Western", "Central"), "MIN": ("Western", "Central"),
    "NSH": ("Western", "Central"), "STL": ("Western", "Central"),
    "WPG": ("Western", "Central"),
    "ANA": ("Western", "Pacific"), "CGY": ("Western", "Pacific"),
    "EDM": ("Western", "Pacific"), "LAK": ("Western", "Pacific"),
    "SJS": ("Western", "Pacific"), "SEA": ("Western", "Pacific"),
    "VAN": ("Western", "Pacific"), "VGK": ("Western", "Pacific"),
}


def build_standings(games_played, all_teams):
    stats = {}
    for abbrev in all_teams:
        stats[abbrev] = {"teamAbbrev": abbrev, "points": 0, "wins": 0, "losses": 0,
                         "otLosses": 0, "regulationWins": 0, "gamesPlayed": 0}
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
    teams = []
    for abbrev, s in stats.items():
        gp = s["gamesPlayed"]
        s["gamesRemaining"] = 82 - gp
        s["pointsPace"] = round(s["points"] / gp * 82, 1) if gp > 0 else 82
        if abbrev in CONF_DIV:
            s["conference"], s["division"] = CONF_DIV[abbrev]
        else:
            s["conference"], s["division"] = "Eastern", "Atlantic"
        teams.append(s)
    return teams


def evaluate_params(k_factor, home_bonus, ot_discount, all_season_data, num_sims=50000):
    """Run all checkpoints with given params and return Brier score."""
    all_preds = []

    for season_id, (all_games, all_teams, actual_playoff) in all_season_data.items():
        total_games = len(all_games)

        for gr_target in CHECKPOINTS:
            target_gp = 82 - gr_target
            game_idx = min(int(target_gp / 82 * total_games), total_games - 1)
            games_played = all_games[:game_idx]
            remaining = all_games[game_idx:]

            if not games_played:
                continue

            teams = build_standings(games_played, all_teams)
            remaining_schedule = [(g["home"], g["away"]) for g in remaining]

            # Compute Elo with these params
            ratings = {}
            for game in games_played:
                h, a = game["home"], game["away"]
                if h not in ratings: ratings[h] = 1500
                if a not in ratings: ratings[a] = 1500
                h_exp = 1.0 / (1.0 + 10.0 ** ((ratings[a] - ratings[h] - home_bonus) / 400.0))
                h_act = 1.0 if game["home_win"] else 0.0
                k = k_factor * (ot_discount if game["overtime"] else 1.0)
                ratings[h] += k * (h_act - h_exp)
                ratings[a] += k * ((1 - h_act) - (1 - h_exp))

            # Set Elo cache
            sim._elo_ratings_cache = ratings

            # Temporarily patch _elo_win_probs to use our home_bonus
            orig_fn = sim._elo_win_probs
            def patched_elo_win_probs(home_idxs, away_idxs, elo_array, hb=home_bonus):
                home_elo = elo_array[home_idxs] + hb
                away_elo = elo_array[away_idxs]
                probs = 1.0 / (1.0 + np.power(10.0, (away_elo - home_elo) / 400.0))
                return np.clip(probs, 0.25, 0.75)
            sim._elo_win_probs = patched_elo_win_probs

            try:
                results = sim.run_simulations_np(teams, num_simulations=num_sims, real_schedule=remaining_schedule)
                for abbrev in results:
                    pct = results[abbrev]["playoff_pct"]
                    actual = 1.0 if abbrev in actual_playoff else 0.0
                    all_preds.append((pct / 100.0, actual))
            except Exception:
                pass

            sim._elo_win_probs = orig_fn

    if not all_preds:
        return 1.0

    brier = np.mean([(p - a) ** 2 for p, a in all_preds])
    return brier


def main():
    # Load cached game data
    all_season_data = {}
    for season_id in SEASONS:
        cache_file = f"/tmp/nhl_games_{season_id}.json"
        if not os.path.exists(cache_file):
            print(f"⚠ Missing cache for {season_id} — run calibration.py first")
            continue
        with open(cache_file) as f:
            all_games = json.load(f)
        all_teams = set()
        for g in all_games:
            all_teams.add(g["home"])
            all_teams.add(g["away"])
        actual_playoff = PLAYOFF_TEAMS.get(season_id, set())
        all_season_data[season_id] = (all_games, all_teams, actual_playoff)
        print(f"✓ Loaded {len(all_games)} games for {season_id}")

    # Grid search
    k_values = [10, 20, 30, 40]
    home_values = [30, 50, 75, 100]
    ot_values = [0.5, 0.75, 1.0]

    print(f"\n🔍 Grid search: {len(k_values)} × {len(home_values)} × {len(ot_values)} = {len(k_values)*len(home_values)*len(ot_values)} combos")
    print(f"   Using 20k sims per checkpoint (fast search)\n")
    sys.stdout.flush()

    best_brier = 1.0
    best_params = None
    results = []

    for k in k_values:
        for hb in home_values:
            for ot in ot_values:
                sim._elo_ratings_cache = None  # Clear cache
                brier = evaluate_params(k, hb, ot, all_season_data, num_sims=20000)
                results.append((k, hb, ot, brier))
                marker = ""
                if brier < best_brier:
                    best_brier = brier
                    best_params = (k, hb, ot)
                    marker = " ⭐ NEW BEST"
                print(f"  K={k:>3} HB={hb:>3} OT={ot:.2f} → Brier={brier:.5f}{marker}")
                sys.stdout.flush()

    print(f"\n{'='*50}")
    print(f"🏆 Best: K={best_params[0]}, Home Bonus={best_params[1]}, OT Discount={best_params[2]}")
    print(f"   Brier Score: {best_brier:.5f}")

    # Show top 10
    results.sort(key=lambda x: x[3])
    print(f"\n📊 Top 10 combos:")
    for k, hb, ot, brier in results[:10]:
        print(f"  K={k:>3} HB={hb:>3} OT={ot:.2f} → {brier:.5f}")

    # Save
    with open("/tmp/elo_tuning_results.json", "w") as f:
        json.dump({"best": {"k": best_params[0], "home_bonus": best_params[1],
                            "ot_discount": best_params[2], "brier": best_brier},
                   "all": [{"k": k, "hb": hb, "ot": ot, "brier": b} for k, hb, ot, b in results]}, f, indent=2)


if __name__ == "__main__":
    main()
