"""
IceChaser History Tracker

Aggregates daily snapshots into a time series of playoff odds per team.
Also computes magic/tragic numbers and schedule strength.
"""

import json
import os
import glob
import numpy as np
from datetime import datetime, timezone

SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "snapshots")
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "odds_history.json")
LIVE_DATA = "/var/www/icechaser/data/playoff_odds.json"


def build_odds_history():
    """
    Aggregate all daily snapshots + current live data into a time series.
    Returns {abbrev: [{date, odds}, ...]} sorted chronologically.
    """
    history = {}  # abbrev -> [{date, odds}]

    # Process all snapshots
    snap_files = sorted(glob.glob(os.path.join(SNAPSHOTS_DIR, "*.json")))

    for snap_path in snap_files:
        date_str = os.path.basename(snap_path).replace(".json", "")
        try:
            with open(snap_path) as f:
                data = json.load(f)
            for team in data.get("teams", []):
                abbrev = team.get("teamAbbrev", "")
                odds = team.get("playoffOdds", 0)
                if abbrev:
                    if abbrev not in history:
                        history[abbrev] = []
                    history[abbrev].append({"date": date_str, "odds": odds})
        except Exception:
            continue

    # Add current live data as today's entry
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with open(LIVE_DATA) as f:
            live = json.load(f)
        for team in live.get("teams", []):
            abbrev = team.get("teamAbbrev", "")
            odds = team.get("playoffOdds", 0)
            if abbrev:
                if abbrev not in history:
                    history[abbrev] = []
                # Don't duplicate if snapshot already has today
                if not history[abbrev] or history[abbrev][-1]["date"] != today:
                    history[abbrev].append({"date": today, "odds": odds})
    except Exception:
        pass

    # Sort each team's history
    for abbrev in history:
        history[abbrev].sort(key=lambda x: x["date"])

    # Always overwrite the 'today' entry with current live odds (snapshots may be stale)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with open(LIVE_DATA) as f:
            live = json.load(f)
        live_odds = {t["teamAbbrev"]: t["playoffOdds"] for t in live.get("teams", [])}
        for abbrev, entries in history.items():
            if abbrev in live_odds:
                # Replace or append today's entry with fresh live data
                if entries and entries[-1]["date"] == today:
                    entries[-1]["odds"] = live_odds[abbrev]
                else:
                    entries.append({"date": today, "odds": live_odds[abbrev]})
    except Exception:
        pass

    return history


def compute_magic_tragic_numbers(teams, conferences):
    """
    Compute magic number (clinch) and tragic number (elimination) for each team.

    Magic number = wins needed + rival losses needed to clinch.
    Formula: magic = (games_remaining of 9th place team) + (9th place points) - (your points) + 1

    For NHL: top 8 per conference make playoffs.
    Tragic number = losses needed + rival wins needed to be eliminated.
    """
    results = {}

    for conf_name in ["Eastern", "Western"]:
        conf_teams = [t for t in teams if t.get("conference") == conf_name]
        conf_teams.sort(key=lambda t: (-t.get("points", 0), -t.get("regulationWins", t.get("wins", 0))))

        if len(conf_teams) < 9:
            continue

        # 8th place is the last playoff spot
        # 9th place team is the first team out
        for i, team in enumerate(conf_teams):
            abbrev = team.get("teamAbbrev", "")
            pts = team.get("points", 0)
            gr = team.get("gamesRemaining", 0)
            max_pts = pts + gr * 2  # max possible points (win all remaining)

            # Magic number: clinch vs 9th place
            if i < 8:
                # You're currently in - magic number is vs the 9th place team
                ninth = conf_teams[8]
                ninth_max = ninth.get("points", 0) + ninth.get("gamesRemaining", 0) * 2
                # You clinch when: your points > ninth_max (impossible for them to catch you)
                # Magic = ninth_max - pts + 1 ... but in terms of combined wins+rival losses:
                magic = ninth.get("points", 0) + ninth.get("gamesRemaining", 0) * 2 - pts + 1
                if magic <= 0:
                    magic = 0  # Already clinched
                results[abbrev] = {
                    "magic_number": max(0, magic),
                    "tragic_number": None,
                    "chasing": ninth.get("teamAbbrev", ""),
                    "position": i + 1,
                }
            else:
                # You're currently out - tragic number is vs 8th place
                eighth = conf_teams[7]
                eighth_pts = eighth.get("points", 0)
                # You're eliminated when: your max_pts < eighth's current points
                # Tragic = max_pts - eighth_pts + 1
                tragic = max_pts - eighth_pts + 1
                if tragic <= 0:
                    tragic = 0  # Already eliminated
                results[abbrev] = {
                    "magic_number": None,
                    "tragic_number": max(0, tragic),
                    "chasing": eighth.get("teamAbbrev", ""),
                    "position": i + 1,
                }

    return results


def compute_schedule_strength(teams, remaining_schedule, elo_ratings):
    """
    Compute strength of remaining schedule for each team using Elo ratings.
    Returns {abbrev: {avg_opponent_elo, rank, hardest_games, easiest_games}}
    """
    if not elo_ratings:
        return {}

    # Count remaining opponents per team
    team_opponents = {}  # abbrev -> [list of opponent elos]

    for home, away in remaining_schedule:
        h_elo = elo_ratings.get(home, 1500)
        a_elo = elo_ratings.get(away, 1500)

        if home not in team_opponents:
            team_opponents[home] = []
        team_opponents[home].append({"opponent": away, "elo": a_elo, "location": "home"})

        if away not in team_opponents:
            team_opponents[away] = []
        team_opponents[away].append({"opponent": home, "elo": h_elo, "location": "away"})

    results = {}
    all_avgs = []

    for abbrev, opponents in team_opponents.items():
        if not opponents:
            continue
        avg_elo = np.mean([o["elo"] for o in opponents])
        all_avgs.append((abbrev, avg_elo))

        # Sort opponents by elo
        sorted_opps = sorted(opponents, key=lambda o: -o["elo"])

        results[abbrev] = {
            "avg_opponent_elo": round(float(avg_elo), 1),
            "games_remaining": len(opponents),
            "hardest_3": [{"vs": o["opponent"], "elo": round(o["elo"], 0), "loc": o["location"]}
                         for o in sorted_opps[:3]],
            "easiest_3": [{"vs": o["opponent"], "elo": round(o["elo"], 0), "loc": o["location"]}
                         for o in sorted_opps[-3:]],
        }

    # Rank by difficulty (highest avg = hardest)
    all_avgs.sort(key=lambda x: -x[1])
    for rank, (abbrev, _) in enumerate(all_avgs):
        if abbrev in results:
            results[abbrev]["difficulty_rank"] = rank + 1

    return results


def save_history(history):
    """Save history to JSON file."""
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "w") as f:
        json.dump({
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "teams": history,
        }, f)
    print(f"   ✓ History saved ({len(history)} teams)")


def main():
    print("📈 Building odds history...")
    history = build_odds_history()
    save_history(history)

    # Print sample
    for abbrev in ["WSH", "OTT", "CBJ"]:
        if abbrev in history:
            h = history[abbrev]
            print(f"   {abbrev}: {len(h)} data points, {h[0]['odds']:.1f}% → {h[-1]['odds']:.1f}%")


if __name__ == "__main__":
    main()
