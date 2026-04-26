"""
Python wrapper for the Rust Monte Carlo simulator.
Calls the compiled binary via subprocess, passing JSON in/out.
"""

import json
import subprocess
import os

RUST_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rust-sim", "target", "release", "icechaser-sim")

CONF_MAP = {"Eastern": 0, "Western": 1}
DIV_MAP = {"Atlantic": 0, "Metropolitan": 1, "Central": 2, "Pacific": 3}


def _teams_to_rust(teams):
    """Convert Python team dicts to Rust format."""
    return [{
        "abbrev": t["teamAbbrev"],
        "points": float(t["points"]),
        "regulation_wins": float(t.get("regulationWins", 0)),
        "points_pace": float(t.get("pointsPace", 0)),
        "conference": CONF_MAP.get(t["conference"], 0),
        "division": DIV_MAP.get(t["division"], 0),
        "games_remaining": t.get("gamesRemaining", 0),
    } for t in teams]


def _schedule_to_rust(schedule, abbrev_to_idx, exclude_pairs=None):
    """Convert schedule to Rust format, optionally excluding certain game pairs."""
    exclude = exclude_pairs or set()
    result = []
    for h, a in schedule:
        if h in abbrev_to_idx and a in abbrev_to_idx and (h, a) not in exclude:
            result.append({"home_idx": abbrev_to_idx[h], "away_idx": abbrev_to_idx[a]})
    return result


def _games_to_rust_active(games, abbrev_to_idx):
    """Convert game dicts to Rust active_games format."""
    result = []
    for g in games:
        h = g["homeTeamAbbrev"]
        a = g["awayTeamAbbrev"]
        if h in abbrev_to_idx and a in abbrev_to_idx:
            result.append({"home_idx": abbrev_to_idx[h], "away_idx": abbrev_to_idx[a]})
    return result


def _call_rust(input_data, timeout=600):
    """Call Rust binary with JSON input, return parsed output."""
    result = subprocess.run(
        [RUST_BIN],
        input=json.dumps(input_data),
        capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(f"Rust sim failed: {result.stderr[:500]}")
    return json.loads(result.stdout)


def run_simulations(teams, num_simulations=10000, real_schedule=None):
    """Run baseline Monte Carlo simulation."""
    abbrev_to_idx = {t["teamAbbrev"]: i for i, t in enumerate(teams)}
    rust_teams = _teams_to_rust(teams)
    rust_schedule = _schedule_to_rust(real_schedule or [], abbrev_to_idx)

    input_data = {
        "teams": rust_teams,
        "schedule": rust_schedule,
        "forced_outcomes": [],
        "num_simulations": num_simulations,
    }

    raw = _call_rust(input_data)

    results = {}
    for r in raw:
        abbrev = r["abbrev"]
        team = next((t for t in teams if t["teamAbbrev"] == abbrev), None)
        clinch_indicator = team.get("clinchIndicator", "") if team else ""
        results[abbrev] = {
            "playoff_pct": r["playoff_pct"],
            "clinched": r["clinched"] or clinch_indicator in ("x", "y", "z", "p"),
            "eliminated": r["eliminated"],
            "sim_count": int(r["playoff_pct"] * num_simulations / 100),
        }
    return results


def run_brute_force(teams, active_games, real_schedule, sims_per_combo=500):
    """
    Run brute force on active games with 4 outcomes per game.
    Excludes active games from remaining schedule to prevent double-counting.
    
    Returns: {
        "teams": {abbrev: {"playoff_pct", "best_case", "worst_case", "game_scenarios": [[4 floats per game]]}},
        "combo_count": int,
        "active_game_list": [(home_abbrev, away_abbrev), ...]
    }
    """
    abbrev_to_idx = {t["teamAbbrev"]: i for i, t in enumerate(teams)}
    idx_to_abbrev = {i: t["teamAbbrev"] for i, t in enumerate(teams)}

    # Exclude active games from schedule
    exclude_pairs = set()
    for g in active_games:
        exclude_pairs.add((g["homeTeamAbbrev"], g["awayTeamAbbrev"]))

    rust_teams = _teams_to_rust(teams)
    rust_schedule = _schedule_to_rust(real_schedule or [], abbrev_to_idx, exclude_pairs)
    rust_active = _games_to_rust_active(active_games, abbrev_to_idx)

    input_data = {
        "teams": rust_teams,
        "schedule": rust_schedule,
        "active_games": rust_active,
        "num_sims_per_combo": sims_per_combo,
        "num_outcomes": 4,
    }

    raw = _call_rust(input_data, timeout=600)

    # Build game list for mapping scenarios
    game_list = [(g["homeTeamAbbrev"], g["awayTeamAbbrev"]) for g in active_games
                 if g["homeTeamAbbrev"] in abbrev_to_idx and g["awayTeamAbbrev"] in abbrev_to_idx]

    team_results = {}
    for r in raw["teams"]:
        team_results[r["abbrev"]] = {
            "playoff_pct": r["playoff_pct"],
            "best_case": r["best_case"],
            "worst_case": r["worst_case"],
            "game_scenarios": r["game_scenarios"],  # [game_idx][4 outcomes]
        }

    return {
        "teams": team_results,
        "combo_count": raw["combo_count"],
        "sims_per_combo": raw["sims_per_combo"],
        "active_game_list": game_list,
    }


def run_what_if(teams, target_abbrev, real_schedule, num_simulations=10000):
    """
    Run What If analysis for a target team.
    Runs full sims, tracks target team's final record in each sim,
    groups by (wins, otl) → frequency + playoff %.
    
    Returns: {
        "abbrev": str,
        "current_points": float,
        "games_left": int,
        "total_sims": int,
        "records": [{"wins", "ot_losses", "reg_losses", "final_points", "times", "made_playoffs", "playoff_pct"}, ...]
    }
    """
    abbrev_to_idx = {t["teamAbbrev"]: i for i, t in enumerate(teams)}
    target_idx = abbrev_to_idx[target_abbrev]
    target_team = teams[target_idx]
    gl = target_team.get("gamesRemaining", 0)

    rust_teams = _teams_to_rust(teams)
    # Pass FULL schedule — the Rust engine tracks which games involve the target
    rust_schedule = _schedule_to_rust(real_schedule or [], abbrev_to_idx)

    input_data = {
        "teams": rust_teams,
        "schedule": rust_schedule,
        "target_idx": target_idx,
        "target_games_left": gl,
        "num_simulations": num_simulations,
    }

    raw = _call_rust(input_data, timeout=600)
    return raw


def run_brute_force_by_conference(teams, active_games, real_schedule, sims_per_combo=500):
    """
    Split active games by conference and run separate brute forces.
    Then fill in cross-conf game scenarios separately.
    """
    tc = {t["teamAbbrev"]: t["conference"] for t in teams}

    east_games = [g for g in active_games
                  if tc.get(g["homeTeamAbbrev"]) == tc.get(g["awayTeamAbbrev"]) == "Eastern"]
    west_games = [g for g in active_games
                  if tc.get(g["homeTeamAbbrev"]) == tc.get(g["awayTeamAbbrev"]) == "Western"]
    cross_games = [g for g in active_games
                   if tc.get(g["homeTeamAbbrev"]) != tc.get(g["awayTeamAbbrev"])]

    # Each run includes its own games + cross-conf
    east_set = east_games + cross_games
    west_set = west_games + cross_games

    team_set = {t["teamAbbrev"] for t in teams}
    n_east = len(east_set)
    n_west = len(west_set)

    print(f"   🔵 Eastern: {n_east} games → 4^{n_east} = {4**n_east} combos")
    print(f"   🟣 Western: {n_west} games → 4^{n_west} = {4**n_west} combos")

    east_result = run_brute_force(teams, east_set, real_schedule, sims_per_combo)
    print(f"   ✓ Eastern done")
    west_result = run_brute_force(teams, west_set, real_schedule, sims_per_combo)
    print(f"   ✓ Western done")

    # For cross-conf games: run a separate brute force just for those games
    cross_result = None
    if cross_games:
        print(f"   🔄 Cross-conf: {len(cross_games)} games → {4**len(cross_games)} combos")
        cross_result = run_brute_force(teams, cross_games, real_schedule, sims_per_combo)
        print(f"   ✓ Cross-conf done")

    # Build merged results
    # Key insight: game_scenarios[i] corresponds to active_game_list[i] in each run.
    # Games may appear in both runs (cross-conf). Dedupe by (home, away) sorted key.
    merged_teams = {}
    for t in teams:
        abbrev = t["teamAbbrev"]
        team_conf = tc.get(abbrev, "")

        # Primary: own conference run
        if team_conf == "Eastern":
            primary = east_result["teams"].get(abbrev, {})
            primary_games = east_result.get("active_game_list", [])
            primary_raw = primary.get("game_scenarios", []) or []
        else:
            primary = west_result["teams"].get(abbrev, {})
            primary_games = west_result.get("active_game_list", [])
            primary_raw = primary.get("game_scenarios", []) or []

        # Cross-result: cross-conf scenarios
        cross_raw = []
        cross_games_list = []
        if cross_result:
            cross_raw = cross_result["teams"].get(abbrev, {}).get("game_scenarios", []) or []
            cross_games_list = cross_result.get("active_game_list", [])

        # Build lookup for cross scenarios by sorted (home, away)
        cross_by_key = {}
        for gi, sc in enumerate(cross_raw):
            if gi < len(cross_games_list):
                key = tuple(sorted(cross_games_list[gi]))
                cross_by_key[key] = sc

        # Build combined, deduped game list and merged scenario array
        combined_games = []
        combined_scenarios = []
        seen_keys = set()

        for gi, game in enumerate(primary_games):
            key = tuple(sorted(game))
            if key not in seen_keys:
                seen_keys.add(key)
                combined_games.append(game)
                combined_scenarios.append(primary_raw[gi] if gi < len(primary_raw) else [])

        for gi, game in enumerate(cross_games_list):
            key = tuple(sorted(game))
            if key not in seen_keys:
                seen_keys.add(key)
                combined_games.append(game)
                combined_scenarios.append(cross_raw[gi] if gi < len(cross_raw) else [])

        merged_teams[abbrev] = {
            "playoff_pct": primary.get("playoff_pct", 0),
            "best_case": primary.get("best_case", 0),
            "worst_case": primary.get("worst_case", 0),
            "game_scenarios": combined_scenarios,
            "combined_game_list": combined_games,
        }

    return {
        "teams": merged_teams,
        "east_games": east_result.get("active_game_list", []),
        "west_games": west_result.get("active_game_list", []),
    }
