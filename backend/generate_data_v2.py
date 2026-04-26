#!/usr/bin/env python3
"""
IceChaser v2 - Generate playoff odds data using Rust sim engine.
Fixes: double-counting, 4 OT outcomes, timezone handling, baseline alignment.
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nhl_api
import simulator_rust as sim
import narrative

# v2 output: project data dir → cron copies to live site
OUTPUT_PATH = "/root/.openclaw/workspace/projects/icechaser/data/playoff_odds.json"
SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "snapshots")
NUM_SIMULATIONS = 10000
SIMS_PER_COMBO = 500


def save_daily_snapshot():
    """Save pre-game snapshot once per day."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = os.path.join(SNAPSHOT_DIR, f"{today}.json")
    if not os.path.exists(path) and os.path.exists(OUTPUT_PATH):
        import shutil
        shutil.copy2(OUTPUT_PATH, path)
        print(f"   📸 Daily snapshot saved: {path}")


def load_previous_odds():
    """Load daily snapshot for delta calculation."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    
    for date_str in [today, yesterday]:
        path = os.path.join(SNAPSHOT_DIR, f"{date_str}.json")
        try:
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                return {t["teamAbbrev"]: {"playoff_pct": t.get("playoffOdds", 0)} for t in data.get("teams", [])}
        except Exception:
            pass
    return None


def get_today_and_tomorrow_games():
    """
    Get today's and tomorrow's games, handling NHL timezone correctly.
    The NHL API gameWeek[0] date is the "current game day" which may be
    yesterday's date if those games just finished.
    
    Logic:
    1. Get the schedule week from the API
    2. Walk through dates: find the first date that has non-FINAL games, that's "today"
    3. If all dates have only FINAL games, use the last FINAL date as "tonight's results"
    4. The date after "today" is "tomorrow"
    """
    teams_raw = nhl_api.get_standings()
    teams = nhl_api.parse_standings(teams_raw)
    
    # Get schedule week (get_schedule_with_fallback returns (raw, parsed_games))
    try:
        schedule, _ = nhl_api.get_schedule_with_fallback()
    except Exception:
        schedule = nhl_api.get_schedule_by_date(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    
    game_week = schedule.get("gameWeek", [])
    
    today_date = None
    today_games = []
    tomorrow_date = None
    tomorrow_games = []
    
    # Find "today" = first date with non-final games, or last date with final games
    last_final_date = None
    for day in game_week:
        date_str = day["date"]
        day_games = day.get("games", [])
        
        has_active = any(g.get("gameState") not in ("FINAL", "OFF") for g in day_games)
        has_any = len(day_games) > 0
        
        if has_any and not has_active:
            last_final_date = date_str
        
        if has_active:
            today_date = date_str
            break
    
    if today_date is None and last_final_date:
        # All games in the week are final — use last final date as "tonight's results"
        today_date = last_final_date
    
    if today_date is None and game_week:
        today_date = game_week[0]["date"]
    
    # Parse today's games from the specific date
    for day in game_week:
        if day["date"] == today_date:
            for g in day.get("games", []):
                today_games.append(_parse_game(g))
            break
    
    # Find tomorrow = next date after today_date in the week
    found_today = False
    for day in game_week:
        if found_today and day.get("games"):
            tomorrow_date = day["date"]
            for g in day.get("games", []):
                tomorrow_games.append(_parse_game(g))
            break
        if day["date"] == today_date:
            found_today = True
    
    # If tomorrow not in this week, fetch next week
    if not tomorrow_games and today_date:
        try:
            next_date = (datetime.strptime(today_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            tmr_raw = nhl_api.get_schedule_by_date(next_date)
            for day in tmr_raw.get("gameWeek", []):
                if day["date"] == next_date:
                    for g in day.get("games", []):
                        tomorrow_games.append(_parse_game(g))
                    break
        except Exception:
            pass
    
    print(f"   📅 Today: {today_date} ({len(today_games)} games), Tomorrow: {tomorrow_date or 'next day'} ({len(tomorrow_games)} games)")
    return teams, today_games, tomorrow_games


def _parse_game(g):
    """Parse a single game from the NHL API schedule format."""
    home = g.get("homeTeam", {})
    away = g.get("awayTeam", {})
    return {
        "gameId": g.get("id", 0),
        "gameDate": "",
        "gameTime": g.get("startTimeUTC", ""),
        "gameState": g.get("gameState", "FUT"),
        "homeTeamAbbrev": home.get("abbrev", ""),
        "homeTeamName": home.get("placeName", {}).get("default", home.get("abbrev", "")),
        "homeScore": home.get("score", 0),
        "awayTeamAbbrev": away.get("abbrev", ""),
        "awayTeamName": away.get("placeName", {}).get("default", away.get("abbrev", "")),
        "awayScore": away.get("score", 0),
        "venue": g.get("venue", {}).get("default", ""),
        "period": g.get("periodDescriptor", {}).get("number", 1),
    }


def organize_by_conference(teams, sim_results):
    """Organize teams into conference/division structure."""
    conferences = defaultdict(lambda: {"divisions": defaultdict(list), "wildcards": []})

    for team in teams:
        conf = team["conference"]
        div = team["division"]
        abbrev = team["teamAbbrev"]
        odds = sim_results.get(abbrev, {})
        team_entry = {**team, "playoffOdds": odds.get("playoff_pct", 0),
                      "clinched": odds.get("clinched", False), "eliminated": odds.get("eliminated", False)}
        conferences[conf]["divisions"][div].append(team_entry)

    result = {}
    for conf_name, conf_data in conferences.items():
        result[conf_name] = {"divisions": {}, "wildcards": []}
        for div_name, div_teams in conf_data["divisions"].items():
            sorted_teams = sorted(div_teams, key=lambda x: (x["points"], x["regulationWins"]), reverse=True)
            for i, team in enumerate(sorted_teams):
                team["divisionRank"] = i + 1
            result[conf_name]["divisions"][div_name] = sorted_teams

        wildcard_candidates = []
        for div_name, div_teams in conf_data["divisions"].items():
            sorted_div = sorted(div_teams, key=lambda x: (x["points"], x["regulationWins"]), reverse=True)
            wildcard_candidates.extend(sorted_div[3:])
        wildcard_candidates.sort(key=lambda x: (x["points"], x["regulationWins"]), reverse=True)
        for i, team in enumerate(wildcard_candidates):
            team["wildcardRank"] = i + 1
        result[conf_name]["wildcards"] = wildcard_candidates[:2]

    return result


def add_game_impact(games, sim_results):
    """Add playoff impact rating to each game."""
    enhanced = []
    for game in games:
        home_pct = sim_results.get(game.get("homeTeamAbbrev", ""), {}).get("playoff_pct", 100)
        away_pct = sim_results.get(game.get("awayTeamAbbrev", ""), {}).get("playoff_pct", 100)
        home_impact = (50 - abs(home_pct - 50)) / 50
        away_impact = (50 - abs(away_pct - 50)) / 50
        impact_score = round((home_impact + away_impact) / 2 * 10, 1)
        label = "CRITICAL" if impact_score >= 8 else "HIGH" if impact_score >= 6 else "MEDIUM" if impact_score >= 4 else "LOW" if impact_score >= 2 else "NONE"
        enhanced.append({**game, "homePlayoffOdds": home_pct, "awayPlayoffOdds": away_pct,
                         "playoffImpactScore": impact_score, "playoffImpactLabel": label})
    enhanced.sort(key=lambda x: x["playoffImpactScore"], reverse=True)
    return enhanced


def build_brute_force_scenarios(bf_result, active_games, teams, sim_results):
    """
    Convert Rust brute force output into per-team scenario dicts and best/worst cases.
    
    Returns: (team_scenarios, best_worst_cases)
    """
    tc = {t["teamAbbrev"]: t["conference"] for t in teams}

    # Per-team game lists: use combined_game_list if available (conference-split merge),
    # otherwise fall back to deduplicated east+west
    team_game_lists = {}
    if "combined_game_list" in next(iter(bf_result.get("teams", {}).values()), {}):
        for abbrev, data in bf_result["teams"].items():
            team_game_lists[abbrev] = data.get("combined_game_list", [])
    else:
        game_list = bf_result.get("east_games", []) + bf_result.get("west_games", [])
        seen = set()
        unique_games = []
        for g in game_list:
            key = (g[0], g[1])
            if key not in seen:
                seen.add(key)
                unique_games.append(g)
        if not unique_games and "active_game_list" in bf_result:
            unique_games = bf_result["active_game_list"]
        for abbrev in bf_result["teams"]:
            team_game_lists[abbrev] = unique_games

    team_scenarios = {t["teamAbbrev"]: [] for t in teams}
    best_worst_cases = {}

    for abbrev, data in bf_result["teams"].items():
        baseline = sim_results.get(abbrev, {}).get("playoff_pct", 0)
        best = data.get("best_case", baseline)
        worst = data.get("worst_case", baseline)
        unique_games = team_game_lists.get(abbrev, [])
        has_game = any(abbrev in (g[0], g[1]) for g in unique_games)

        best_worst_cases[abbrev] = {
            "best": best,
            "worst": worst,
            "medium": round((best + worst) / 2, 1),
            "has_game": has_game,
        }

        # Build per-game scenarios from brute force averages
        game_scenarios_raw = data.get("game_scenarios", [])
        team_conf = tc.get(abbrev)

        for g_idx, (home, away) in enumerate(unique_games):
            if g_idx >= len(game_scenarios_raw):
                continue
            
            is_own_game = (abbrev == home or abbrev == away)
            game_conf = set()
            if home in tc: game_conf.add(tc[home])
            if away in tc: game_conf.add(tc[away])
            
            if not is_own_game and team_conf not in game_conf:
                continue
            
            s = game_scenarios_raw[g_idx]  # [home_reg, away_reg, home_ot, away_ot]
            if len(s) < 4:
                continue
            
            # FIX: delta against team's pre-game baseline odds, not per-game average.
            # Makes UI intuitive: +5 means "5 pts better than before tonight's games."
            max_delta = max(abs(v - baseline) for v in s)
            impact = "high" if max_delta >= 3 else "medium" if max_delta >= 1 else "low"

            team_scenarios[abbrev].append({
                "game_id": f"{home}_{away}",
                "home_team": home,
                "away_team": away,
                "home_team_name": home,
                "away_team_name": away,
                "is_own_game": is_own_game,
                # 4 outcome display values
                "if_home_reg_win_pct": round(s[0], 1),
                "if_away_reg_win_pct": round(s[1], 1),
                "if_home_ot_win_pct": round(s[2], 1),
                "if_away_ot_win_pct": round(s[3], 1),
                # Deltas vs team's pre-game baseline odds
                "home_delta_reg": round(s[0] - baseline, 1),
                "away_delta_reg": round(s[1] - baseline, 1),
                "home_delta_ot": round(s[2] - baseline, 1),
                "away_delta_ot": round(s[3] - baseline, 1),
                # Legacy
                "if_home_wins_pct": round(s[0], 1),
                "if_away_wins_pct": round(s[1], 1),
                "home_delta": round(s[0] - baseline, 1),
                "away_delta": round(s[1] - baseline, 1),
                "impact": impact,
            })
        
        # Sort: own game first, then by highest delta
        team_scenarios[abbrev].sort(key=lambda x: (
            0 if x.get("is_own_game") else 1,
            -max(abs(x["home_delta_reg"]), abs(x["away_delta_reg"]),
                 abs(x.get("home_delta_ot", 0)), abs(x.get("away_delta_ot", 0)))
        ))
    
    # Fill in missing teams with baseline
    for t in teams:
        abbrev = t["teamAbbrev"]
        if abbrev not in best_worst_cases:
            baseline = sim_results.get(abbrev, {}).get("playoff_pct", 0)
            best_worst_cases[abbrev] = {"best": baseline, "worst": baseline, "medium": baseline, "has_game": False}
    
    # Fix: if a team has 0 actual scenarios after filtering, their best/worst
    # must equal baseline — no game tonight affects them
    for abbrev in team_scenarios:
        if len(team_scenarios[abbrev]) == 0 and abbrev in best_worst_cases:
            baseline = sim_results.get(abbrev, {}).get("playoff_pct", 0)
            best_worst_cases[abbrev] = {"best": baseline, "worst": baseline, "medium": baseline, "has_game": False}
    
    return team_scenarios, best_worst_cases


def build_flat_teams_list(teams, sim_results, today_scenarios=None, today_bw=None,
                          tmr_scenarios=None, tmr_bw=None, what_if_data=None):
    """Build flat list of all teams with odds and scenarios."""
    result = []
    for team in teams:
        abbrev = team["teamAbbrev"]
        odds = sim_results.get(abbrev, {})
        entry = {
            **team,
            "playoffOdds": odds.get("playoff_pct", 0),
            "clinched": odds.get("clinched", False),
            "eliminated": odds.get("eliminated", False),
        }
        
        # Today's scenarios
        entry["game_scenarios"] = (today_scenarios or {}).get(abbrev, [])
        bwc = (today_bw or {}).get(abbrev)
        if bwc:
            entry["best_case_tonight"] = bwc["best"]
            entry["medium_case_tonight"] = bwc["medium"]
            entry["worst_case_tonight"] = bwc["worst"]
            entry["has_game_tonight"] = bwc["has_game"]
        else:
            entry["best_case_tonight"] = entry["playoffOdds"]
            entry["medium_case_tonight"] = entry["playoffOdds"]
            entry["worst_case_tonight"] = entry["playoffOdds"]
            entry["has_game_tonight"] = False
        
        # Tomorrow's scenarios
        entry["tomorrow_scenarios"] = (tmr_scenarios or {}).get(abbrev, [])
        tbw = (tmr_bw or {}).get(abbrev)
        if tbw:
            entry["best_case_tomorrow"] = tbw["best"]
            entry["worst_case_tomorrow"] = tbw["worst"]
            entry["has_game_tomorrow"] = tbw["has_game"]
        
        # What If table
        wif = (what_if_data or {}).get(abbrev)
        if wif and "records" in wif:
            total_sims = wif.get("total_sims", 10000)
            what_if_table = []
            for rec in wif["records"]:
                if rec.get("times", 0) == 0:
                    continue
                what_if_table.append({
                    "wins": rec["wins"],
                    "losses": rec.get("reg_losses", 0),
                    "otl": rec.get("ot_losses", 0),
                    "final_points": rec["final_points"],
                    "times": rec["times"],
                    "made_playoffs": rec.get("made_playoffs", 0),
                    "playoff_pct": rec["playoff_pct"],
                })
            # Sort: wins desc, then otl desc
            what_if_table.sort(key=lambda r: (-r["wins"], -r["otl"]))
            entry["what_if"] = what_if_table
            entry["what_if_total_sims"] = total_sims
        
        result.append(entry)
    
    result.sort(key=lambda x: x["playoffOdds"], reverse=True)
    return result


def run_brute_force_for_games(label, teams, games, real_schedule, sim_results):
    """Run brute force on a set of games, with conference split if needed."""
    active = [g for g in games if g.get("gameState") not in ("FINAL", "OFF")]
    
    if not active:
        print(f"   No active games for {label} — using baseline")
        scenarios = {t["teamAbbrev"]: [] for t in teams}
        bw = {t["teamAbbrev"]: {
            "best": sim_results.get(t["teamAbbrev"], {}).get("playoff_pct", 0),
            "worst": sim_results.get(t["teamAbbrev"], {}).get("playoff_pct", 0),
            "medium": sim_results.get(t["teamAbbrev"], {}).get("playoff_pct", 0),
            "has_game": False,
        } for t in teams}
        return scenarios, bw
    
    n = len(active)
    combos = 4 ** n
    
    if combos > 100000:
        print(f"   ⚡ {label}: {n} games → {combos} combos, splitting by conference...")
        bf = sim.run_brute_force_by_conference(teams, active, real_schedule, SIMS_PER_COMBO)
    else:
        print(f"   🔢 {label}: {n} games → {combos} combos × {SIMS_PER_COMBO} sims")
        bf = sim.run_brute_force(teams, active, real_schedule, SIMS_PER_COMBO)
        bf["east_games"] = bf.get("active_game_list", [])
        bf["west_games"] = []
    
    return build_brute_force_scenarios(bf, active, teams, sim_results)


def main():
    print("🏒 IceChaser v2 - Generating playoff odds (Rust engine)...")
    
    save_daily_snapshot()
    
    print("📂 Loading previous odds...")
    previous_odds = load_previous_odds()
    
    print("🌐 Fetching NHL data...")
    teams, today_games, tomorrow_games = get_today_and_tomorrow_games()
    print(f"   ✓ {len(teams)} teams, {len(today_games)} today, {len(tomorrow_games)} tomorrow")
    
    print("📅 Fetching remaining schedule...")
    real_schedule = nhl_api.get_remaining_schedule()
    print(f"   ✓ {len(real_schedule)} remaining games")
    
    print(f"🎲 Running {NUM_SIMULATIONS:,} baseline simulations...")
    sim_results = sim.run_simulations(teams, NUM_SIMULATIONS, real_schedule)
    print(f"   ✓ Baseline complete")
    
    print("📝 Generating narratives...")
    narratives = narrative.generate_all_narratives(teams, sim_results, today_games, previous_odds)
    
    # Today's brute force
    print("🔮 Today's scenarios...")
    today_scenarios, today_bw = run_brute_force_for_games("today", teams, today_games, real_schedule, sim_results)
    
    # Tomorrow's brute force
    print("🔮 Tomorrow's scenarios...")
    tmr_scenarios, tmr_bw = run_brute_force_for_games("tomorrow", teams, tomorrow_games, real_schedule, sim_results)
    
    # What If tables for bubble teams
    print("🤔 What If tables...")
    what_if_data = {}
    bubble_teams = [t for t in teams if not sim_results.get(t["teamAbbrev"], {}).get("clinched", False)
                    and not sim_results.get(t["teamAbbrev"], {}).get("eliminated", False)]
    for i, t in enumerate(bubble_teams):
        abbrev = t["teamAbbrev"]
        gl = t.get("gamesRemaining", 0)
        if gl == 0:
            continue
        try:
            wif = sim.run_what_if(teams, abbrev, real_schedule, num_simulations=10000)
            what_if_data[abbrev] = wif
        except Exception as e:
            print(f"   ⚠️ What If failed for {abbrev}: {e}")
    print(f"   ✓ What If computed for {len(what_if_data)} teams")

    # Organize data
    print("🗂️  Organizing data...")
    conferences = organize_by_conference(teams, sim_results)
    enhanced_today = add_game_impact(today_games, sim_results)
    enhanced_tomorrow = add_game_impact(tomorrow_games, sim_results)
    flat_teams = build_flat_teams_list(teams, sim_results, today_scenarios, today_bw, tmr_scenarios, tmr_bw, what_if_data)
    
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": "20252026",
        "num_simulations": NUM_SIMULATIONS,
        "narratives": narratives,
        "conferences": conferences,
        "teams": flat_teams,
        "todays_games": enhanced_today,
        "tomorrows_games": enhanced_tomorrow,
    }
    
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"✅ Done! Output written to: {OUTPUT_PATH}")
    
    clinched = sum(1 for t in flat_teams if t["clinched"])
    eliminated = sum(1 for t in flat_teams if t["eliminated"])
    bubble = sum(1 for t in flat_teams if 20 <= t["playoffOdds"] <= 80)
    print(f"\n📊 Summary: {clinched} clinched, {eliminated} eliminated, {bubble} on bubble")
    print(f"   Headline: {narratives['headline'][:80]}...")


if __name__ == "__main__":
    main()
