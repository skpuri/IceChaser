"""
IceChaser Elo Rating Engine

Fetches all completed games from the NHL API and computes Elo ratings
for all 32 teams. Ratings are stored in a persistent JSON file and
updated incrementally after each game.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
import urllib.request

# Constants
INITIAL_ELO = 1500
K_FACTOR = 10          # Calibrated: NHL teams are very stable, slow movement is better
HOME_BONUS = 100       # Calibrated: strong home ice — ~64% expected for equal teams
OT_DISCOUNT = 0.50     # Calibrated: OT wins are near coin-flips, barely move ratings
SEASON_START = "2025-10-07"
ELO_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "elo_ratings.json")

def fetch_week_games(date_str):
    """Fetch one week of games from the NHL schedule API."""
    url = f"https://api-web.nhle.com/v1/schedule/{date_str}"
    req = urllib.request.Request(url, headers={"User-Agent": "IceChaser/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    
    games = []
    next_date = data.get("nextStartDate")
    
    for day in data.get("gameWeek", []):
        for game in day.get("games", []):
            state = game.get("gameState", "")
            if state not in ("OFF", "FINAL"):
                continue
            if game.get("gameType") != 2:  # Regular season only
                continue
            
            home = game.get("homeTeam", {})
            away = game.get("awayTeam", {})
            outcome = game.get("gameOutcome", {})
            
            home_score = home.get("score", 0)
            away_score = away.get("score", 0)
            last_period = outcome.get("lastPeriodType", "REG")
            
            games.append({
                "id": game["id"],
                "date": day["date"],
                "home": home.get("abbrev", ""),
                "away": away.get("abbrev", ""),
                "home_score": home_score,
                "away_score": away_score,
                "overtime": last_period in ("OT", "SO"),
                "home_win": home_score > away_score,
            })
    
    return games, next_date


def fetch_all_season_games():
    """Fetch every completed regular season game from Oct 7 to now."""
    all_games = []
    current_date = SEASON_START
    today = datetime.utcnow().strftime("%Y-%m-%d")
    seen_ids = set()
    
    print(f"📡 Fetching season games from {SEASON_START}...")
    
    while current_date and current_date <= today:
        games, next_date = fetch_week_games(current_date)
        for g in games:
            if g["id"] not in seen_ids:
                seen_ids.add(g["id"])
                all_games.append(g)
        
        if not next_date or next_date <= current_date:
            break
        current_date = next_date
        time.sleep(0.1)  # Be polite to the API
    
    all_games.sort(key=lambda g: (g["date"], g["id"]))
    print(f"   ✓ {len(all_games)} completed games fetched")
    return all_games


def expected_score(rating_a, rating_b):
    """Elo expected score for team A vs team B."""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def compute_elo_ratings(games, initial_ratings=None):
    """
    Replay all games and compute Elo ratings.
    
    Args:
        games: list of game dicts sorted by date
        initial_ratings: optional dict of {abbrev: rating} to start from
    
    Returns:
        ratings: dict of {abbrev: current_elo}
        history: list of (game_id, date, {abbrev: elo_after}) for each game
    """
    ratings = dict(initial_ratings) if initial_ratings else {}
    history = []
    
    for game in games:
        home = game["home"]
        away = game["away"]
        
        # Initialize teams we haven't seen
        if home not in ratings:
            ratings[home] = INITIAL_ELO
        if away not in ratings:
            ratings[away] = INITIAL_ELO
        
        # Expected scores (with home ice advantage)
        home_expected = expected_score(ratings[home] + HOME_BONUS, ratings[away])
        away_expected = 1.0 - home_expected
        
        # Actual scores
        if game["home_win"]:
            home_actual = 1.0
            away_actual = 0.0
        else:
            home_actual = 0.0
            away_actual = 1.0
        
        # OT discount: OT/SO wins move ratings less
        k = K_FACTOR
        if game["overtime"]:
            k *= OT_DISCOUNT
        
        # Update ratings
        ratings[home] += k * (home_actual - home_expected)
        ratings[away] += k * (away_actual - away_expected)
        
        history.append({
            "game_id": game["id"],
            "date": game["date"],
            "home": home,
            "away": away,
            "home_win": game["home_win"],
            "overtime": game["overtime"],
            "home_elo_after": round(ratings[home], 1),
            "away_elo_after": round(ratings[away], 1),
        })
    
    return ratings, history


def win_probability(home_elo, away_elo):
    """
    Compute P(home wins) from Elo ratings.
    Home ice bonus is baked into the calculation.
    """
    return expected_score(home_elo + HOME_BONUS, away_elo)


def save_ratings(ratings, history, games_processed):
    """Save current ratings to persistent file."""
    os.makedirs(os.path.dirname(ELO_FILE), exist_ok=True)
    data = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "games_processed": games_processed,
        "k_factor": K_FACTOR,
        "home_bonus": HOME_BONUS,
        "ot_discount": OT_DISCOUNT,
        "ratings": {k: round(v, 1) for k, v in sorted(ratings.items())},
        "last_10_games": history[-10:] if history else [],
    }
    with open(ELO_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"   ✓ Ratings saved to {ELO_FILE}")
    return data


def load_ratings():
    """Load existing ratings from file."""
    if os.path.exists(ELO_FILE):
        with open(ELO_FILE) as f:
            return json.load(f)
    return None


def get_elo_ratings():
    """
    Public API: Get current Elo ratings dict.
    Loads from file if available, otherwise computes from scratch.
    Returns dict of {abbrev: elo_rating}.
    """
    saved = load_ratings()
    if saved:
        return saved["ratings"]
    
    # Full rebuild
    games = fetch_all_season_games()
    ratings, history = compute_elo_ratings(games)
    save_ratings(ratings, history, len(games))
    return {k: round(v, 1) for k, v in ratings.items()}


def update_ratings():
    """
    Incremental update: fetch only new games since last update.
    Called by cron to keep ratings fresh.
    """
    saved = load_ratings()
    
    if saved:
        last_processed = saved["games_processed"]
        print(f"📊 Current ratings: {last_processed} games processed")
    else:
        last_processed = 0
        print("📊 No existing ratings — full rebuild")
    
    # Always fetch full season (API is fast, ensures correctness)
    games = fetch_all_season_games()
    
    if len(games) == last_processed:
        print("   ⏭ No new games — ratings unchanged")
        return saved["ratings"] if saved else {}
    
    print(f"   🔄 {len(games) - last_processed} new games to process")
    ratings, history = compute_elo_ratings(games)
    save_ratings(ratings, history, len(games))
    
    # Print top/bottom 5
    sorted_teams = sorted(ratings.items(), key=lambda x: -x[1])
    print("\n   Top 5:")
    for abbrev, elo in sorted_teams[:5]:
        print(f"     {abbrev}: {elo:.0f}")
    print("   Bottom 5:")
    for abbrev, elo in sorted_teams[-5:]:
        print(f"     {abbrev}: {elo:.0f}")
    
    return {k: round(v, 1) for k, v in ratings.items()}


if __name__ == "__main__":
    ratings = update_ratings()
    print(f"\n📊 All {len(ratings)} teams rated")
