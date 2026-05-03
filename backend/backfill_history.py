#!/usr/bin/env python3
"""
IceChaser Historical Backfill — Generate snapshots for the full 2025-26 season.

Fetches historical NHL standings from api-web.nhle.com for each date,
runs a lightweight Monte Carlo sim, and saves the snapshot.

Usage:
  cd /root/.openclaw/workspace/projects/icechaser
  python3 backend/backfill_history.py

Options:
  --start YYYY-MM-DD   Start date (default: 2025-10-07, season opener)
  --end YYYY-MM-DD     End date (default: 2026-03-31)
  --sims N             Simulations per date (default: 10000)
  --force              Overwrite existing snapshots
  --dry-run            Fetch standings but don't run sims, just show team count
"""

import os
import sys
import json
import time
import argparse
import requests
from datetime import datetime, timedelta, timezone

# Add backend to path
BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

import nhl_api
import simulator_np as simulator

SNAPSHOTS_DIR = os.path.join(BACKEND_DIR, "..", "data", "snapshots")
BASE_URL = "https://api-web.nhle.com/v1"
HEADERS = {"User-Agent": "IceChaser/1.0"}


def fetch_standings_by_date(date_str):
    """Fetch NHL standings for a specific date."""
    url = f"{BASE_URL}/standings/{date_str}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        return None
    return resp.json()


def fetch_remaining_schedule_from_date(date_str, end_date_str="2026-04-18"):
    """
    Fetch all games from date_str through end of regular season.
    Returns list of (home_abbrev, away_abbrev) tuples.
    Uses weekly schedule pages, stepping 7 days at a time.
    """
    all_games = []
    seen_ids = set()
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    end = datetime.strptime(end_date_str, "%Y-%m-%d").date()

    while d <= end:
        try:
            url = f"{BASE_URL}/schedule/{d.strftime('%Y-%m-%d')}"
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                d += timedelta(days=7)
                continue
            data = resp.json()
            for day in data.get("gameWeek", []):
                day_date = day.get("date", "")
                # Only include games on or after our target date
                if day_date < date_str:
                    continue
                for game in day.get("games", []):
                    gid = game.get("id", 0)
                    if gid in seen_ids:
                        continue
                    seen_ids.add(gid)
                    # Only regular season games (gameType 2)
                    if game.get("gameType", 2) != 2:
                        continue
                    state = game.get("gameState", "")
                    # For historical dates, completed games show as OFF/FINAL
                    # We want future games relative to the backfill date
                    home = game.get("homeTeam", {}).get("abbrev", "")
                    away = game.get("awayTeam", {}).get("abbrev", "")
                    if home and away and day_date > date_str:
                        all_games.append((home, away))
            d += timedelta(days=7)
            time.sleep(0.3)  # Be nice to the API
        except Exception as e:
            print(f"      ⚠ Schedule fetch error for {d}: {e}")
            d += timedelta(days=7)
            continue

    return all_games


def run_backfill(start_date, end_date, num_sims=10000, force=False, dry_run=False):
    """Run the full backfill from start_date to end_date."""
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

    d = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    total_days = (end - d).days + 1
    processed = 0
    skipped = 0
    errors = 0

    print(f"🏒 IceChaser Historical Backfill")
    print(f"   Range: {start_date} → {end_date} ({total_days} days)")
    print(f"   Sims per date: {num_sims}")
    print(f"   Output: {SNAPSHOTS_DIR}")
    print(f"   Force overwrite: {force}")
    print()

    while d <= end:
        date_str = d.strftime("%Y-%m-%d")
        snap_path = os.path.join(SNAPSHOTS_DIR, f"{date_str}.json")

        # Skip if already exists
        if os.path.exists(snap_path) and not force:
            skipped += 1
            d += timedelta(days=1)
            continue

        print(f"📅 {date_str} ({processed+1}/{total_days})...", end=" ", flush=True)

        # Fetch standings
        try:
            raw = fetch_standings_by_date(date_str)
            if not raw or not raw.get("standings"):
                print("⏭ no standings (off-day or preseason)")
                d += timedelta(days=1)
                continue
            teams = nhl_api.parse_standings(raw)
            if not teams:
                print("⏭ no teams parsed")
                d += timedelta(days=1)
                continue
        except Exception as e:
            print(f"❌ standings error: {e}")
            errors += 1
            d += timedelta(days=1)
            time.sleep(1)
            continue

        # Check if season has started (teams should have games played)
        total_gp = sum(t.get("gamesPlayed", 0) for t in teams)
        if total_gp == 0:
            print("⏭ season not started yet")
            d += timedelta(days=1)
            continue

        if dry_run:
            print(f"✓ {len(teams)} teams, {total_gp} total GP (dry run)")
            d += timedelta(days=1)
            processed += 1
            continue

        # Fetch remaining schedule from this date
        try:
            remaining_schedule = fetch_remaining_schedule_from_date(date_str)
        except Exception as e:
            print(f"⚠ schedule error: {e}, using synthetic")
            remaining_schedule = None

        # Run simulation
        try:
            # Set the schedule for the simulator
            simulator.run_simulations_np._real_schedule = remaining_schedule
            sim_results = simulator.run_simulations_np(
                teams, num_simulations=num_sims, real_schedule=remaining_schedule
            )
        except Exception as e:
            print(f"❌ sim error: {e}")
            errors += 1
            d += timedelta(days=1)
            continue

        # Build snapshot in the same format as generate_data_v3.py output
        team_list = []
        for team in teams:
            abbrev = team["teamAbbrev"]
            sr = sim_results.get(abbrev, {})
            pct = sr.get("playoff_pct", 0.0)
            team_list.append({
                "teamAbbrev": abbrev,
                "teamName": team.get("teamName", ""),
                "teamCommonName": team.get("teamCommonName", ""),
                "conference": team.get("conference", ""),
                "division": team.get("division", ""),
                "gamesPlayed": team.get("gamesPlayed", 0),
                "wins": team.get("wins", 0),
                "losses": team.get("losses", 0),
                "otLosses": team.get("otLosses", 0),
                "points": team.get("points", 0),
                "regulationWins": team.get("regulationWins", 0),
                "gamesRemaining": team.get("gamesRemaining", 0),
                "pointsPace": team.get("pointsPace", 0),
                "playoffOdds": round(pct, 1),
                "clinched": sr.get("clinched", False),
                "eliminated": sr.get("eliminated", False),
            })

        snapshot = {
            "generated_at": f"{date_str}T07:00:00+00:00",
            "season": "20252026",
            "num_simulations": num_sims,
            "teams": team_list,
        }

        # Save
        with open(snap_path, "w") as f:
            json.dump(snapshot, f, indent=2)

        # Quick summary
        top3 = sorted(team_list, key=lambda t: -t["playoffOdds"])[:3]
        summary = ", ".join(f"{t['teamAbbrev']}={t['playoffOdds']}%" for t in top3)
        sched_info = f"{len(remaining_schedule)} games" if remaining_schedule else "synthetic"
        print(f"✓ {len(teams)} teams, sched={sched_info}, top: {summary}")

        processed += 1
        d += timedelta(days=1)
        time.sleep(0.5)  # Rate limit

    print()
    print(f"✅ Backfill complete!")
    print(f"   Processed: {processed}")
    print(f"   Skipped (existing): {skipped}")
    print(f"   Errors: {errors}")
    print()
    print(f"Now run generate_data_v3.py to rebuild odds_history.json with the new snapshots.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IceChaser Historical Backfill")
    parser.add_argument("--start", default="2025-10-07", help="Start date (default: 2025-10-07)")
    parser.add_argument("--end", default="2026-03-31", help="End date (default: 2026-03-31)")
    parser.add_argument("--sims", type=int, default=10000, help="Sims per date (default: 10000)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing snapshots")
    parser.add_argument("--dry-run", action="store_true", help="Fetch standings only, don't simulate")
    args = parser.parse_args()

    run_backfill(args.start, args.end, args.sims, args.force, args.dry_run)
