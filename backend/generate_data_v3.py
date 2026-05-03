#!/usr/bin/env python3
"""
IceChaser - Generate playoff odds data.
Fetches NHL data, runs Monte Carlo simulation, generates narratives,
and writes output to /data/playoff_odds.json.
"""

import json
import os
import sys
from datetime import datetime, timezone
from collections import defaultdict

# Add backend dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nhl_api
import simulator_np as simulator
import narrative

OUTPUT_PATH = "/var/www/icechaser/data/playoff_odds.json"
OUTPUT_PATH_V2 = "/var/www/icechaser-v2/data/playoff_odds.json"  # legacy, kept in sync

NUM_SIMULATIONS = 5000  # TEMP: reduced for low-memory host (was 10000)
NUM_SCENARIO_SIMULATIONS = 1000  # faster scenario sims


DAILY_SNAPSHOT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data", "snapshots"
)


def load_previous_odds():
    """
    Load the DAILY snapshot (pre-game odds) for delta calculation.
    Falls back to the current output file if no snapshot exists.
    """
    # Try today's snapshot first (saved at first run of the day)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot_path = os.path.join(DAILY_SNAPSHOT_DIR, f"{today}.json")
    
    # If no snapshot for today, use yesterday's
    if not os.path.exists(snapshot_path):
        from datetime import timedelta
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        snapshot_path = os.path.join(DAILY_SNAPSHOT_DIR, f"{yesterday}.json")
    
    for path in [snapshot_path, OUTPUT_PATH]:
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    data = json.load(f)
                prev = {}
                for team_data in data.get("teams", []):
                    prev[team_data["teamAbbrev"]] = {
                        "playoff_pct": team_data.get("playoffOdds", 0)
                    }
                if prev:
                    return prev
        except Exception:
            pass
    return None


def save_daily_snapshot():
    """
    Save a daily snapshot of odds BEFORE today's games.
    Only saves once per day (doesn't overwrite).
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    os.makedirs(DAILY_SNAPSHOT_DIR, exist_ok=True)
    snapshot_path = os.path.join(DAILY_SNAPSHOT_DIR, f"{today}.json")
    
    if os.path.exists(snapshot_path):
        return  # Already saved today
    
    # Copy current output as today's snapshot
    if os.path.exists(OUTPUT_PATH):
        import shutil
        shutil.copy2(OUTPUT_PATH, snapshot_path)
        print(f"   📸 Daily snapshot saved: {snapshot_path}")


def organize_by_conference(teams, sim_results):
    """Organize teams into conference/division structure."""
    conferences = defaultdict(lambda: {"divisions": defaultdict(list), "wildcards": []})

    # Group by conference → division
    for team in teams:
        conf = team["conference"]
        div = team["division"]
        abbrev = team["teamAbbrev"]
        odds = sim_results.get(abbrev, {})

        team_entry = {
            **team,
            "playoffOdds": odds.get("playoff_pct", 0),
            "clinched": odds.get("clinched", False),
            "eliminated": odds.get("eliminated", False),
        }
        conferences[conf]["divisions"][div].append(team_entry)

    # Sort divisions and find wildcards
    result = {}
    for conf_name, conf_data in conferences.items():
        result[conf_name] = {"divisions": {}, "wildcards": []}
        division_qualifiers = set()

        for div_name, div_teams in conf_data["divisions"].items():
            sorted_teams = sorted(
                div_teams,
                key=lambda x: (x["points"], x["regulationWins"]),
                reverse=True
            )
            # Add rank within division
            for i, team in enumerate(sorted_teams):
                team["divisionRank"] = i + 1

            result[conf_name]["divisions"][div_name] = sorted_teams

            # Top 3 are division qualifiers
            for team in sorted_teams[:3]:
                division_qualifiers.add(team["teamAbbrev"])

        # Wildcards: remaining conf teams sorted by points
        wildcard_candidates = []
        for div_name, div_teams in conf_data["divisions"].items():
            sorted_div = sorted(div_teams, key=lambda x: (x["points"], x["regulationWins"]), reverse=True)
            wildcard_candidates.extend(sorted_div[3:])

        wildcard_candidates.sort(key=lambda x: (x["points"], x["regulationWins"]), reverse=True)
        for i, team in enumerate(wildcard_candidates):
            team["wildcardRank"] = i + 1

        result[conf_name]["wildcards"] = wildcard_candidates[:2]

    return result


def add_game_impact(today_games, sim_results):
    """Add playoff impact rating to each game."""
    enhanced = []
    for game in today_games:
        home_pct = sim_results.get(game["homeTeamAbbrev"], {}).get("playoff_pct", 100)
        away_pct = sim_results.get(game["awayTeamAbbrev"], {}).get("playoff_pct", 100)

        # Impact: how close to 50% are each team's odds?
        home_impact = (50 - abs(home_pct - 50)) / 50
        away_impact = (50 - abs(away_pct - 50)) / 50
        impact_score = round((home_impact + away_impact) / 2 * 10, 1)  # 0-10 scale

        enhanced.append({
            **game,
            "homePlayoffOdds": home_pct,
            "awayPlayoffOdds": away_pct,
            "playoffImpactScore": impact_score,
            "playoffImpactLabel": _impact_label(impact_score),
        })

    # Sort by impact
    enhanced.sort(key=lambda x: x["playoffImpactScore"], reverse=True)
    return enhanced


def _impact_label(score):
    if score >= 8:
        return "CRITICAL"
    elif score >= 6:
        return "HIGH"
    elif score >= 4:
        return "MEDIUM"
    elif score >= 2:
        return "LOW"
    return "NONE"


def run_game_scenarios(teams, today_games, baseline_results):
    """
    For each of today's games, run 4 scenario sims:
      - Home wins regulation  (home+2, away+0)
      - Away wins regulation  (away+2, home+0)
      - Home wins overtime    (home+2, away+1 consolation)
      - Away wins overtime    (away+2, home+1 consolation)

    Returns a dict: {team_abbrev: [list of game_scenario dicts]}
    Team's own game is always included and sorted first.
    """
    if not today_games:
        return {}

    print(f"🔮 Running game scenarios for {len(today_games)} games ({NUM_SCENARIO_SIMULATIONS} sims each × 4 outcomes)...")

    # For each game: 4 scenario sims
    # key: (home_abbrev, away_abbrev, winner_abbrev, ot_game_bool) -> full sim_results
    game_sim_results = {}

    for i, game in enumerate(today_games):
        home = game["homeTeamAbbrev"]
        away = game["awayTeamAbbrev"]

        # Skip games already finished (FINAL/OFF). Include LIVE — still has outcome pending.
        if game.get("gameState") in ("FINAL", "OFF"):
            continue

        print(f"   [{i+1}/{len(today_games)}] {away} @ {home}")

        # Sim 1: home wins regulation (away gets 0)
        key = (home, away, home, False)
        game_sim_results[key] = simulator.run_simulations_with_forced_result(
            teams, home, away, home, ot_game=False, num_simulations=NUM_SCENARIO_SIMULATIONS
        )

        # Sim 2: away wins regulation (home gets 0)
        key = (home, away, away, False)
        game_sim_results[key] = simulator.run_simulations_with_forced_result(
            teams, home, away, away, ot_game=False, num_simulations=NUM_SCENARIO_SIMULATIONS
        )

        # Sim 3: home wins OT (away gets 1 consolation)
        key = (home, away, home, True)
        game_sim_results[key] = simulator.run_simulations_with_forced_result(
            teams, home, away, home, ot_game=True, num_simulations=NUM_SCENARIO_SIMULATIONS
        )

        # Sim 4: away wins OT (home gets 1 consolation)
        key = (home, away, away, True)
        game_sim_results[key] = simulator.run_simulations_with_forced_result(
            teams, home, away, away, ot_game=True, num_simulations=NUM_SCENARIO_SIMULATIONS
        )

    # Build a lookup: team abbrev -> conference
    team_conference = {t["teamAbbrev"]: t["conference"] for t in teams}

    # Build per-team scenario lists
    team_scenarios = {t["teamAbbrev"]: [] for t in teams}

    for game in today_games:
        home = game["homeTeamAbbrev"]
        away = game["awayTeamAbbrev"]
        game_id = str(game.get("gameId", f"{home}_{away}"))

        if game.get("gameState") in ("FINAL", "OFF"):
            continue

        key_home_reg = (home, away, home, False)
        key_away_reg = (home, away, away, False)
        key_home_ot  = (home, away, home, True)
        key_away_ot  = (home, away, away, True)

        if key_home_reg not in game_sim_results:
            continue

        # Determine which conferences are represented in this game
        game_conferences = set()
        if home in team_conference:
            game_conferences.add(team_conference[home])
        if away in team_conference:
            game_conferences.add(team_conference[away])

        for team in teams:
            abbrev = team["teamAbbrev"]
            team_conf = team_conference.get(abbrev)

            # Check if this is the team's own game
            is_own_game = (abbrev == home or abbrev == away)

            # Conference boundary check: skip if neither participant is same-conference.
            # Exception: always include the team's own game.
            if not is_own_game and team_conf not in game_conferences:
                continue

            baseline_pct = baseline_results.get(abbrev, {}).get("playoff_pct", 0)

            if_home_reg = game_sim_results[key_home_reg].get(abbrev, {}).get("playoff_pct", 0)
            if_away_reg = game_sim_results[key_away_reg].get(abbrev, {}).get("playoff_pct", 0)
            if_home_ot  = game_sim_results[key_home_ot].get(abbrev, {}).get("playoff_pct", 0)
            if_away_ot  = game_sim_results[key_away_ot].get(abbrev, {}).get("playoff_pct", 0)

            home_delta_reg = if_home_reg - baseline_pct
            away_delta_reg = if_away_reg - baseline_pct
            home_ot_delta  = if_home_ot  - baseline_pct
            away_ot_delta  = if_away_ot  - baseline_pct

            # Impact based on max absolute delta across all 4 scenarios
            max_abs_delta = max(
                abs(home_delta_reg), abs(away_delta_reg),
                abs(home_ot_delta),  abs(away_ot_delta)
            )

            if max_abs_delta >= 3:
                impact = "high"
            elif max_abs_delta >= 1:
                impact = "medium"
            else:
                impact = "low"

            team_scenarios[abbrev].append({
                "game_id": game_id,
                "home_team": home,
                "away_team": away,
                "home_team_name": game.get("homeTeamName", home),
                "away_team_name": game.get("awayTeamName", away),
                "is_own_game": is_own_game,
                # Raw 0-1 probabilities
                "if_home_reg_win": round(if_home_reg / 100, 4),
                "if_away_reg_win": round(if_away_reg / 100, 4),
                "if_home_ot_win":  round(if_home_ot  / 100, 4),
                "if_away_ot_win":  round(if_away_ot  / 100, 4),
                # Percentage display values
                "if_home_reg_win_pct": round(if_home_reg, 1),
                "if_away_reg_win_pct": round(if_away_reg, 1),
                "if_home_ot_win_pct":  round(if_home_ot,  1),
                "if_away_ot_win_pct":  round(if_away_ot,  1),
                # Deltas vs baseline
                "home_delta_reg": round(home_delta_reg, 1),
                "away_delta_reg": round(away_delta_reg, 1),
                "home_ot_delta":  round(home_ot_delta,  1),
                "away_ot_delta":  round(away_ot_delta,  1),
                # Legacy fields for backward compat (use reg-win values)
                "if_home_wins":     round(if_home_reg / 100, 4),
                "if_away_wins":     round(if_away_reg / 100, 4),
                "if_home_wins_pct": round(if_home_reg, 1),
                "if_away_wins_pct": round(if_away_reg, 1),
                "home_delta":       round(home_delta_reg, 1),
                "away_delta":       round(away_delta_reg, 1),
                "impact": impact,
            })

    # Sort each team's scenarios: own game first, then by highest delta
    for abbrev in team_scenarios:
        team_scenarios[abbrev].sort(
            key=lambda s: (
                0 if s.get("is_own_game") else 1,
                -max(
                    abs(s["home_delta_reg"]), abs(s["away_delta_reg"]),
                    abs(s["home_ot_delta"]),  abs(s["away_ot_delta"])
                )
            )
        )

    print(f"   ✓ Scenarios complete")
    # Also return the raw regulation sim results keyed (home, away, winner) for best/worst case use
    raw_reg_results = {}
    for (home, away, winner, ot), results in game_sim_results.items():
        if not ot:  # only regulation outcomes for best/worst case
            raw_reg_results[(home, away, winner)] = results
    return team_scenarios, raw_reg_results


def _run_brute_force_by_conference(standings, active_games, all_abbrevs, team_conference, baseline_results=None):
    """
    Split games by conference and run separate brute forces.
    Eastern teams only care about Eastern games (+ cross-conf).
    Western teams only care about Western games (+ cross-conf).
    """
    east_games = []
    west_games = []
    cross_games = []
    
    for g in active_games:
        h_conf = team_conference.get(g["homeTeamAbbrev"])
        a_conf = team_conference.get(g["awayTeamAbbrev"])
        if h_conf == a_conf == "Eastern":
            east_games.append(g)
        elif h_conf == a_conf == "Western":
            west_games.append(g)
        else:
            cross_games.append(g)
    
    # Eastern gets east + cross, Western gets west + cross
    east_set = east_games + cross_games
    west_set = west_games + cross_games
    
    print(f"   Eastern: {len(east_set)} games (3^{len(east_set)}={3**len(east_set)} combos)")
    print(f"   Western: {len(west_set)} games (3^{len(west_set)}={3**len(west_set)} combos)")
    
    east_abbrevs = {a for a in all_abbrevs if team_conference.get(a) == "Eastern"}
    west_abbrevs = {a for a in all_abbrevs if team_conference.get(a) == "Western"}
    
    # Run Eastern brute force
    print("   🔵 Running Eastern conference...")
    east_bw, east_sc = run_brute_force_scenarios(standings, east_set, conference_split=False, baseline_results=baseline_results)
    
    # Run Western brute force
    print("   🟣 Running Western conference...")
    west_bw, west_sc = run_brute_force_scenarios(standings, west_set, conference_split=False, baseline_results=baseline_results)
    
    # Merge: each team gets results from their conference's run
    merged_bw = {}
    merged_sc = {}
    for abbrev in all_abbrevs:
        if abbrev in east_abbrevs:
            merged_bw[abbrev] = east_bw.get(abbrev, {"best": 0, "worst": 0, "has_game": False})
            merged_sc[abbrev] = east_sc.get(abbrev, [])
        else:
            merged_bw[abbrev] = west_bw.get(abbrev, {"best": 0, "worst": 0, "has_game": False})
            merged_sc[abbrev] = west_sc.get(abbrev, [])
    
    return merged_bw, merged_sc


def run_brute_force_scenarios(standings, todays_games, conference_split=True, baseline_results=None):
    """
    Brute-force every possible combination of tonight's game outcomes.
    For N games, that's 2^N combos. Each combo gets sim runs.
    
    Returns TWO things:
    1. best_worst: {team_abbrev: {"best": pct, "worst": pct, "has_game": bool}}
    2. game_scenarios: {team_abbrev: [list of per-game scenario dicts]}
       Each game scenario shows the AVERAGE odds across all combos where
       home won vs all combos where away won. This guarantees consistency
       with best/worst case (same data source).
    """
    # Filter to active games only (not FINAL/OFF)
    active_games = [g for g in todays_games if g.get("gameState") not in ("FINAL", "OFF")]
    
    all_abbrevs = [t["teamAbbrev"] for t in standings]
    team_conference = {t["teamAbbrev"]: t["conference"] for t in standings}
    
    if not active_games:
        # No active games — use pre-computed baseline, don't run noisy separate sim
        best_worst = {}
        for team in standings:
            abbrev = team["teamAbbrev"]
            if baseline_results and abbrev in baseline_results:
                pct = baseline_results[abbrev].get("playoff_pct", 0)
            else:
                pct = 0
            best_worst[abbrev] = {"best": pct, "worst": pct, "has_game": False}
        return best_worst, {abbrev: [] for abbrev in all_abbrevs}
    
    # If too many games, split by conference to reduce combos
    n_games = len(active_games)
    if conference_split and 3 ** n_games > 10000:
        print(f"   ⚡ Too many combos (3^{n_games}={3**n_games}), splitting by conference...")
        return _run_brute_force_by_conference(standings, active_games, all_abbrevs, team_conference, baseline_results)
    
    # 3 outcomes per game: home wins reg, away wins reg, OT game
    n_combos = 3 ** n_games
    sims_per_combo = max(2000, min(10000, 1000000 // max(n_combos, 1)))
    
    print(f"   🔢 Brute force: {n_games} games → {n_combos} combos × {sims_per_combo} sims each")
    
    # Which teams play tonight?
    teams_playing = set()
    for g in active_games:
        teams_playing.add(g["homeTeamAbbrev"])
        teams_playing.add(g["awayTeamAbbrev"])
    
    # Track best/worst per team
    team_best = {a: -1.0 for a in all_abbrevs}
    team_worst = {a: 101.0 for a in all_abbrevs}
    
    # Track per-game sums for averaging
    # outcome_key: 'home_reg', 'away_reg', 'home_ot', 'away_ot'
    # (OT combos: we model OT as "home wins OT" or "away wins OT" but they're
    #  combined into one OT combo where we let the sim decide OT winner)
    game_sums = []
    for _ in range(n_games):
        game_sums.append({a: {
            'home_reg': [0.0, 0],
            'away_reg': [0.0, 0],
            'ot': [0.0, 0],  # OT game (loser gets 1pt, sim decides winner)
        } for a in all_abbrevs})
    
    # Iterate all combos using base-3: 0=home_reg, 1=away_reg, 2=OT
    for combo_idx in range(n_combos):
        forced = []
        combo_outcomes = []  # 0, 1, or 2 per game
        
        temp = combo_idx
        for game_idx, game in enumerate(active_games):
            outcome = temp % 3
            temp //= 3
            combo_outcomes.append(outcome)
            
            home = game["homeTeamAbbrev"]
            away = game["awayTeamAbbrev"]
            
            if outcome == 0:  # home wins regulation
                forced.append({"home": home, "away": away, "winner": home, "ot_game": False})
            elif outcome == 1:  # away wins regulation
                forced.append({"home": home, "away": away, "winner": away, "ot_game": False})
            else:  # OT — home wins OT (away gets consolation pt)
                # We alternate: even combo = home wins OT, odd = away wins OT
                # This gives equal representation
                ot_winner = home if (combo_idx % 2 == 0) else away
                forced.append({"home": home, "away": away, "winner": ot_winner, "ot_game": True})
        
        # Run sim
        result = simulator.run_simulations_with_multiple_forced(
            standings, forced, num_simulations=sims_per_combo
        )
        
        # Update best/worst and per-game sums
        for abbrev in all_abbrevs:
            pct = result.get(abbrev, {}).get("playoff_pct", 0)
            if pct > team_best[abbrev]:
                team_best[abbrev] = pct
            if pct < team_worst[abbrev]:
                team_worst[abbrev] = pct
            
            # Accumulate per-game averages
            for game_idx in range(n_games):
                outcome = combo_outcomes[game_idx]
                if outcome == 0:
                    key = 'home_reg'
                elif outcome == 1:
                    key = 'away_reg'
                else:
                    key = 'ot'
                game_sums[game_idx][abbrev][key][0] += pct
                game_sums[game_idx][abbrev][key][1] += 1
        
        if (combo_idx + 1) % 100 == 0 or combo_idx == n_combos - 1:
            print(f"   [{combo_idx + 1}/{n_combos}] combos complete...")
    
    # Build best/worst result
    best_worst = {}
    for abbrev in all_abbrevs:
        best_worst[abbrev] = {
            "best": round(team_best[abbrev], 1),
            "worst": round(team_worst[abbrev], 1),
            "has_game": abbrev in teams_playing,
        }
    
    # Build per-game scenario results (derived from brute force averages)
    # Baseline = overall average across ALL combos
    baseline_sums = {a: 0.0 for a in all_abbrevs}
    for game_idx in range(n_games):
        pass  # we need actual baseline from the main sim, passed in later
    
    team_scenarios = {a: [] for a in all_abbrevs}
    for game_idx, game in enumerate(active_games):
        home = game["homeTeamAbbrev"]
        away = game["awayTeamAbbrev"]
        game_id = str(game.get("gameId", f"{home}_{away}"))
        
        game_conferences = set()
        if home in team_conference:
            game_conferences.add(team_conference[home])
        if away in team_conference:
            game_conferences.add(team_conference[away])
        
        for abbrev in all_abbrevs:
            is_own_game = (abbrev == home or abbrev == away)
            team_conf = team_conference.get(abbrev)
            
            # Conference filter
            if not is_own_game and team_conf not in game_conferences:
                continue
            
            # Get averages from brute force
            hr_sum, hr_count = game_sums[game_idx][abbrev]['home_reg']
            ar_sum, ar_count = game_sums[game_idx][abbrev]['away_reg']
            ot_sum, ot_count = game_sums[game_idx][abbrev]['ot']
            
            if_home_reg_pct = round(hr_sum / hr_count, 1) if hr_count > 0 else 0
            if_away_reg_pct = round(ar_sum / ar_count, 1) if ar_count > 0 else 0
            if_ot_pct = round(ot_sum / ot_count, 1) if ot_count > 0 else 0
            
            # FIX: delta vs team's pre-game baseline odds, not per-game average
            baseline_pct = baseline_results.get(abbrev, {}).get("playoff_pct", 0) if baseline_results else 0
            home_delta_reg = round(if_home_reg_pct - baseline_pct, 1)
            away_delta_reg = round(if_away_reg_pct - baseline_pct, 1)
            ot_delta = round(if_ot_pct - baseline_pct, 1)
            
            max_abs_delta = max(abs(home_delta_reg), abs(away_delta_reg), abs(ot_delta))
            if max_abs_delta >= 3:
                impact = "high"
            elif max_abs_delta >= 1:
                impact = "medium"
            else:
                impact = "low"
            
            team_scenarios[abbrev].append({
                "game_id": game_id,
                "home_team": home,
                "away_team": away,
                "home_team_name": game.get("homeTeamName", home),
                "away_team_name": game.get("awayTeamName", away),
                "is_own_game": is_own_game,
                # 3-outcome display values
                "if_home_reg_win_pct": if_home_reg_pct,
                "if_away_reg_win_pct": if_away_reg_pct,
                "if_ot_pct": if_ot_pct,
                # Deltas
                "home_delta_reg": home_delta_reg,
                "away_delta_reg": away_delta_reg,
                "ot_delta": ot_delta,
                # Legacy compat (map reg values)
                "if_home_wins_pct": if_home_reg_pct,
                "if_away_wins_pct": if_away_reg_pct,
                "if_home_wins": round(if_home_reg_pct / 100, 4),
                "if_away_wins": round(if_away_reg_pct / 100, 4),
                "home_delta": home_delta_reg,
                "away_delta": away_delta_reg,
                "impact": impact,
            })
    
    # Sort: own game first, then by highest delta
    for abbrev in team_scenarios:
        team_scenarios[abbrev].sort(
            key=lambda s: (
                0 if s.get("is_own_game") else 1,
                -max(abs(s["home_delta_reg"]), abs(s["away_delta_reg"]))
            )
        )
    
    print(f"   ✓ Brute force complete")
    return best_worst, team_scenarios


def build_flat_teams_list(teams, sim_results, team_scenarios=None, best_worst_cases=None,
                          tomorrow_scenarios=None, tomorrow_best_worst=None, what_if_data=None):
    """Build flat list of all teams with odds."""
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
        if team_scenarios is not None:
            entry["game_scenarios"] = team_scenarios.get(abbrev, [])
            # Use the compound best/worst from the vectorized simulator — authoritative.
            if best_worst_cases is not None and abbrev in best_worst_cases:
                bwc = best_worst_cases[abbrev]
                entry["best_case_tonight"] = bwc["best"]
                entry["worst_case_tonight"] = bwc["worst"]
                entry["medium_case_tonight"] = bwc.get("medium", round((bwc["best"] + bwc["worst"]) / 2, 1))
                entry["has_game_tonight"] = bwc.get("has_game", False)
            else:
                entry["best_case_tonight"] = entry["playoffOdds"]
                entry["medium_case_tonight"] = entry["playoffOdds"]
                entry["worst_case_tonight"] = entry["playoffOdds"]
                entry["has_game_tonight"] = False
        elif best_worst_cases is not None and abbrev in best_worst_cases:
            bwc = best_worst_cases[abbrev]
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
        if tomorrow_scenarios is not None:
            entry["tomorrow_scenarios"] = tomorrow_scenarios.get(abbrev, [])
        if tomorrow_best_worst is not None and abbrev in tomorrow_best_worst:
            tbw = tomorrow_best_worst[abbrev]
            entry["best_case_tomorrow"] = tbw["best"]
            entry["worst_case_tomorrow"] = tbw["worst"]
            entry["has_game_tomorrow"] = tbw["has_game"]

        # What If finish table — new format: list of dicts directly from vectorized sim
        wif = (what_if_data or {}).get(abbrev)
        if wif:
            # New format: list of dicts with wins/losses/otl/final_points/times/made_playoffs/playoff_pct
            if isinstance(wif, list):
                what_if_table = sorted(wif, key=lambda r: (-r["wins"], -r["otl"]))
                total_sims = 100000
            else:
                # Legacy Rust format
                records = wif.get("records", [])
                total_sims = wif.get("total_sims", 100000)
                what_if_table = []
                for r in records:
                    what_if_table.append({
                        "wins": r.get("wins", 0),
                        "losses": r.get("reg_losses", 0),
                        "otl": r.get("ot_losses", 0),
                        "final_points": r.get("final_points", 0),
                        "times": r.get("times", 0),
                        "made_playoffs": r.get("made_playoffs", 0),
                        "playoff_pct": r.get("playoff_pct", 0),
                    })
                what_if_table.sort(key=lambda r: (-r["wins"], -r["otl"]))
            entry["what_if"] = what_if_table
            entry["what_if_total_sims"] = total_sims

        result.append(entry)

    result.sort(key=lambda x: x["playoffOdds"], reverse=True)
    return result


def main():
    print("🏒 IceChaser - Generating playoff odds...")

    # Save daily snapshot (before regenerating) — first run of the day only
    save_daily_snapshot()

    # Load previous odds for delta calculation
    print("📂 Loading previous odds...")
    previous_odds = load_previous_odds()

    # Fetch data from NHL API
    print("🌐 Fetching NHL standings and schedule...")
    try:
        teams, today_games = nhl_api.get_all_data()
        print(f"   ✓ {len(teams)} teams loaded")
        print(f"   ✓ {len(today_games)} games today")
    except Exception as e:
        print(f"   ✗ Error fetching NHL data: {e}")
        raise

    # If all games are already FINAL and we last updated within 15 minutes, skip
    # (proceed even on off-days to update standings odds)
    terminal_states = {"FINAL", "OFF", "OVER"}
    live_states = {"LIVE", "CRIT", "PRE", "FUT"}
    all_final = all(g.get("gameState", "") in terminal_states for g in today_games)
    any_live = any(g.get("gameState", "") in live_states for g in today_games)
    # On off-days (no games), always update to reflect latest standings
    # On game days with all final, skip within 60 min to avoid redundant writes
    if today_games and all_final:
        import time as _time
        try:
            last_mod = os.path.getmtime(OUTPUT_PATH)
            mins_ago = (_time.time() - last_mod) / 60
            if mins_ago < 60:
                print(f"⏭️  All games final, last updated {mins_ago:.0f}m ago — skipping.")
                sys.exit(0)
        except OSError:
            pass  # file doesn't exist yet, proceed

    if not teams:
        print("   ✗ No team data returned — aborting.")
        sys.exit(1)

    # Fetch real remaining schedule
    print("📅 Fetching remaining schedule...")
    try:
        real_schedule = nhl_api.get_remaining_schedule()
        print(f"   ✓ {len(real_schedule)} remaining games found")
        # Store on the module so forced sims can access it
        simulator.run_simulations_np._real_schedule = real_schedule
    except Exception as e:
        print(f"   ⚠ Could not fetch schedule, using synthetic: {e}")
        real_schedule = None
        simulator.run_simulations_np._real_schedule = None

    # Run Monte Carlo simulation
    # Prepend tonight's games to real_schedule so they're included in the base sim
    # (tonight's games are LIVE/in-progress so not returned by get_remaining_schedule)
    full_schedule = real_schedule or []
    # Only include games that haven't finished yet — FINAL/OFF games are already
    # reflected in the standings from the NHL API, re-simulating them gives wrong odds
    active_tonight = [g for g in today_games if g.get("gameState", "") in ("FUT", "PRE", "LIVE", "CRIT")]
    tonight_pairs = set((g["homeTeamAbbrev"], g["awayTeamAbbrev"]) for g in active_tonight)
    tonight_as_tuples = [(g["homeTeamAbbrev"], g["awayTeamAbbrev"]) for g in active_tonight]
    full_schedule_with_tonight = tonight_as_tuples + [g for g in full_schedule if (g[0], g[1]) not in tonight_pairs]
    simulator.run_simulations_np._real_schedule = full_schedule_with_tonight

    # Update Elo ratings before running sim
    print("📊 Updating Elo ratings...")
    try:
        import elo_engine
        elo_engine.update_ratings()
    except Exception as e:
        print(f"   ⚠ Elo update failed, falling back to points pace: {e}")

    # Single 100k vectorized sim: produces odds + scenarios + best/worst + what-if
    print("🎲🔮📈 Running 100k vectorized simulation (Elo-based, odds + scenarios + what-if)...")
    team_scenarios = {}
    best_worst_cases = {}
    what_if_data = {}
    sim_results = {}
    try:
        vect_best_worst, vect_scenarios, vect_odds, vect_what_if, vect_seed_probs = simulator.run_scenario_analysis_vectorized(
            teams, active_tonight, num_simulations=500000, real_schedule=real_schedule
        )
        team_scenarios = vect_scenarios
        what_if_data = vect_what_if
        seed_probs_data = vect_seed_probs
        for abbrev, data in vect_best_worst.items():
            best_worst_cases[abbrev] = {
                "best": data["best"],
                "medium": round((data["best"] + data["worst"]) / 2, 1),
                "worst": data["worst"],
                "has_game": data["has_game"],
            }
        # Build sim_results from vect_odds (replaces the old 10k base sim entirely)
        for team in teams:
            abbrev = team["teamAbbrev"]
            pct = vect_odds.get(abbrev, 0.0)
            sim_results[abbrev] = {
                "playoff_pct": pct,
                "clinched": pct >= 99.5 or team.get("clinchIndicator", "") in ("x", "y", "z", "p"),
                "eliminated": pct <= 0.05,
                "sim_count": 100000,
            }
        print(f"   ✓ 100k simulation complete")
    except Exception as e:
        print(f"   ✗ Simulation error: {e}")
        import traceback
        traceback.print_exc()
        raise

    # Generate narratives using the 100k sim results
    print("📝 Generating narratives...")
    try:
        narratives = narrative.generate_all_narratives(
            teams, sim_results, today_games, previous_odds
        )
        print(f"   ✓ Narratives generated")
    except Exception as e:
        print(f"   ✗ Narrative error: {e}")
        raise
    except Exception as e:
        print(f"   ✗ Scenario analysis error: {e}")
        import traceback
        traceback.print_exc()
        team_scenarios = {}
        best_worst_cases = {}

    # Fetch tomorrow's games and run brute force scenarios
    print("📅 Fetching tomorrow's schedule...")
    tomorrow_scenarios = {}
    tomorrow_best_worst = {}
    tomorrow_enhanced = []
    try:
        tomorrow_games = nhl_api.get_tomorrow_games()
        tomorrow_enhanced = add_game_impact(tomorrow_games, sim_results) if tomorrow_games else []
        print(f"   ✓ {len(tomorrow_enhanced)} games tomorrow")
        
        if tomorrow_games:
            print("🔮 Running vectorized scenario analysis for tomorrow's games...")
            # For tomorrow's scenarios, exclude tonight's games from the future schedule
            # (tonight's results are already reflected in standings via the NHL API)
            # Build schedule: tomorrow's games + remaining future (not tonight)
            tomorrow_pairs = set((g["homeTeamAbbrev"], g["awayTeamAbbrev"]) for g in tomorrow_games)
            post_tomorrow_schedule = [g for g in (real_schedule or []) if (g[0], g[1]) not in tonight_pairs and (g[0], g[1]) not in tomorrow_pairs]
            tomorrow_full_schedule = [(g["homeTeamAbbrev"], g["awayTeamAbbrev"]) for g in tomorrow_games] + post_tomorrow_schedule
            tmr_bw, tmr_sc, _, _tmr_wif, _ = simulator.run_scenario_analysis_vectorized(
                teams, tomorrow_games, num_simulations=500000, real_schedule=tomorrow_full_schedule
            )
            tomorrow_scenarios = tmr_sc
            for abbrev, data in tmr_bw.items():
                tomorrow_best_worst[abbrev] = data
            print(f"   ✓ Tomorrow's scenarios complete")
    except Exception as e:
        print(f"   ⚠ Could not fetch tomorrow's games: {e}")
        import traceback
        traceback.print_exc()

    # What If tables already computed in the 100k vectorized sim above
    print(f"🤔 What If tables: {len(what_if_data)} teams (from 100k sim)")

    # Organize data
    print("🗂️  Organizing data...")
    conferences = organize_by_conference(teams, sim_results)
    enhanced_games = add_game_impact(today_games, sim_results)
    flat_teams = build_flat_teams_list(teams, sim_results, team_scenarios, best_worst_cases,
                                       tomorrow_scenarios, tomorrow_best_worst, what_if_data)

    # Attach seed probability distributions
    for t in flat_teams:
        sp = seed_probs_data.get(t["teamAbbrev"], {}) if 'seed_probs_data' in dir() else {}
        if sp:
            t["seed_probs"] = sp

    # Compute magic/tragic numbers, schedule strength, and odds history
    print("📊 Computing magic numbers, schedule strength, history...")
    try:
        import history_tracker
        import elo_engine as _elo

        # Magic/tragic numbers
        magic_tragic = history_tracker.compute_magic_tragic_numbers(flat_teams, conferences)
        for t in flat_teams:
            mt = magic_tragic.get(t["teamAbbrev"], {})
            if mt:
                t["magic_number"] = mt.get("magic_number")
                t["tragic_number"] = mt.get("tragic_number")
                t["conf_position"] = mt.get("position")
                t["chasing_team"] = mt.get("chasing")

        # Schedule strength
        elo_data = _elo.load_ratings()
        elo_rats = elo_data["ratings"] if elo_data else {}
        sched_strength = history_tracker.compute_schedule_strength(
            flat_teams, full_schedule_with_tonight, elo_rats
        )
        for t in flat_teams:
            ss = sched_strength.get(t["teamAbbrev"], {})
            if ss:
                t["avg_opponent_elo"] = ss.get("avg_opponent_elo")
                t["schedule_difficulty_rank"] = ss.get("difficulty_rank")
                t["hardest_remaining"] = ss.get("hardest_3", [])
                t["easiest_remaining"] = ss.get("easiest_3", [])

        # Odds history
        odds_history = history_tracker.build_odds_history()
        for t in flat_teams:
            h = odds_history.get(t["teamAbbrev"], [])
            if h:
                t["odds_history"] = h
                if len(h) >= 2:
                    t["odds_7d_ago"] = h[max(0, len(h)-8)]["odds"] if len(h) > 7 else h[0]["odds"]

        print(f"   ✓ Magic numbers: {sum(1 for t in flat_teams if t.get('magic_number') is not None)} teams")
        print(f"   ✓ Schedule strength: {len(sched_strength)} teams")
        print(f"   ✓ History: {len(odds_history)} teams")
    except Exception as e:
        print(f"   ⚠ Enhanced data failed: {e}")
        import traceback; traceback.print_exc()

    # Clinch/elimination scenarios, projected standings, draft lottery, tweets
    print("💡 Computing clinch scenarios, projections, draft lottery...")
    try:
        import advanced_features as adv

        # Clinch/elimination scenarios
        clinch_elim = adv.compute_clinch_elimination_scenarios(
            flat_teams, enhanced_games, sim_results, conferences
        )
        for t in flat_teams:
            ce = clinch_elim.get(t["teamAbbrev"], {})
            if ce:
                t["clinch_scenarios"] = ce.get("clinch_scenarios", [])
                t["elimination_scenarios"] = ce.get("elimination_scenarios", [])

        # Projected standings
        projections = adv.compute_projected_standings(flat_teams, sim_results, what_if_data)
        for t in flat_teams:
            proj = projections.get(t["teamAbbrev"], {})
            if proj:
                t["projected_points"] = proj.get("projected_points")
                t["projected_wins"] = proj.get("projected_wins")
                t["projected_seed"] = proj.get("projected_seed")
                t["projected_record"] = proj.get("projected_record")
                t["points_p10"] = proj.get("points_p10")
                t["points_p90"] = proj.get("points_p90")

        # Draft lottery
        draft_odds = adv.compute_draft_lottery_odds(flat_teams, sim_results, projections)
        for t in flat_teams:
            dl = draft_odds.get(t["teamAbbrev"], {})
            if dl:
                t["draft_lottery"] = dl

        # Game tweets
        tweets = adv.generate_game_tweets(flat_teams, enhanced_games, sim_results)

        print(f"   ✓ Clinch scenarios: {sum(1 for t in flat_teams if t.get('clinch_scenarios'))} teams")
        print(f"   ✓ Projections: {len(projections)} teams")
        print(f"   ✓ Draft lottery: {len(draft_odds)} teams")
        print(f"   ✓ Tweets: {len(tweets)} games")
    except Exception as e:
        print(f"   ⚠ Advanced features failed: {e}")
        import traceback; traceback.print_exc()
        tweets = []

    # Build output JSON
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": "20252026",
        "num_simulations": NUM_SIMULATIONS,
        "narratives": narratives,
        "conferences": conferences,
        "teams": flat_teams,
        "todays_games": enhanced_games,
        "tomorrows_games": tomorrow_enhanced,
        "game_tweets": tweets if 'tweets' in dir() else [],
    }

    # Write output
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    # Keep legacy v2 path in sync
    try:
        os.makedirs(os.path.dirname(OUTPUT_PATH_V2), exist_ok=True)
        import shutil as _shutil
        _shutil.copy2(OUTPUT_PATH, OUTPUT_PATH_V2)
    except Exception:
        pass

    print(f"✅ Done! Output written to: {OUTPUT_PATH}")

    # Write separate odds_history.json for the chart explorer
    try:
        chart_history_path = "/var/www/icechaser/data/odds_history.json"
        dates = set()
        teams_chart = {}
        for team in flat_teams:
            ab = team.get("teamAbbrev", "")
            if not ab: continue
            th = {}
            for e in team.get("odds_history", []):
                d = e.get("date", ""); o = e.get("odds", 0)
                if d: dates.add(d); th[d] = o
            if th: teams_chart[ab] = th
        chart_data = {"dates": sorted(dates), "teams": teams_chart}
        with open(chart_history_path, "w") as f:
            json.dump(chart_data, f)
        print(f"📈 Chart history updated ({len(sorted(dates))} dates)")
    except Exception as e:
        print(f"⚠️ Chart history update failed: {e}")

    # Print summary
    print("\n📊 Quick Summary:")
    print(f"   Teams: {len(flat_teams)}")
    print(f"   Games today: {len(enhanced_games)}")
    clinched = sum(1 for t in flat_teams if t["clinched"])
    eliminated = sum(1 for t in flat_teams if t["eliminated"])
    bubble = sum(1 for t in flat_teams if 20 <= t["playoffOdds"] <= 80)
    print(f"   Clinched: {clinched}")
    print(f"   Eliminated: {eliminated}")
    print(f"   Bubble (20-80%): {bubble}")
    print(f"\n   Headline: {narratives['headline'][:80]}...")

    return output


if __name__ == "__main__":
    main()
    # Generate static SEO pages after data update
    try:
        import static_pages
        static_pages.main()
    except Exception as e:
        print(f"   ⚠ Static page generation failed: {e}")
    # Update analytics
    try:
        import analytics
        analytics.main()
    except Exception as e:
        print(f"   ⚠ Analytics update failed: {e}")

    try:
        import generate_infographics
        generate_infographics.generate_all_infographics()
    except Exception as e:
        print(f"   ⚠ Infographic generation failed: {e}")

    print(f"\n✅ All done!")
