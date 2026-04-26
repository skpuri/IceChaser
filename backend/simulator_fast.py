"""
High-performance Monte Carlo playoff simulator using Numba JIT + NumPy.
All hot paths compiled to machine code.
"""

import numpy as np
from numba import njit, prange
from collections import defaultdict
import multiprocessing as mp
from functools import partial

HOME_WIN_PROB = 0.54
OT_PROBABILITY = 0.24


@njit(cache=True)
def _simulate_batch(
    base_points, base_reg_wins,
    home_idxs, away_idxs,
    home_probs, n_teams, n_games, n_sims,
    team_conf, team_div, n_confs, n_divs, div_to_conf
):
    """
    Core simulation loop — fully JIT compiled.
    Returns playoff_counts array (n_teams,).
    """
    playoff_counts = np.zeros(n_teams, dtype=np.int64)
    
    for sim in range(n_sims):
        # Initialize points for this sim
        sim_pts = base_points.copy()
        sim_reg = base_reg_wins.copy()
        
        # Simulate all games
        for g in range(n_games):
            h = home_idxs[g]
            a = away_idxs[g]
            
            home_wins = np.random.random() < home_probs[g]
            goes_to_ot = np.random.random() < OT_PROBABILITY
            
            if home_wins:
                sim_pts[h] += 2.0
                if goes_to_ot:
                    sim_pts[a] += 1.0  # OTL consolation
                else:
                    sim_reg[h] += 1.0  # regulation win
            else:
                sim_pts[a] += 2.0
                if goes_to_ot:
                    sim_pts[h] += 1.0
                else:
                    sim_reg[a] += 1.0
        
        # Determine playoff qualifiers
        for conf_idx in range(n_confs):
            # Get divisions in this conference
            for pass_num in range(2):
                # Pass 0: find division top-3
                # Pass 1: find wildcards from remaining
                
                if pass_num == 0:
                    # For each division, find top 3
                    for div_idx in range(n_divs):
                        if div_to_conf[div_idx] != conf_idx:
                            continue
                        
                        # Collect teams in this division
                        div_team_count = 0
                        div_teams_local = np.empty(16, dtype=np.int64)
                        div_scores = np.empty(16, dtype=np.float64)
                        
                        for t in range(n_teams):
                            if team_conf[t] == conf_idx and team_div[t] == div_idx:
                                div_teams_local[div_team_count] = t
                                div_scores[div_team_count] = sim_pts[t] * 10000 + sim_reg[t]
                                div_team_count += 1
                        
                        # Sort descending (simple insertion sort — small N)
                        for i in range(div_team_count):
                            for j in range(i + 1, div_team_count):
                                if div_scores[j] > div_scores[i]:
                                    div_scores[i], div_scores[j] = div_scores[j], div_scores[i]
                                    div_teams_local[i], div_teams_local[j] = div_teams_local[j], div_teams_local[i]
                        
                        # Top 3 qualify
                        for k in range(min(3, div_team_count)):
                            playoff_counts[div_teams_local[k]] += 1
                
                elif pass_num == 1:
                    # Wildcard: collect all non-top-3 conference teams, pick top 2
                    wc_count = 0
                    wc_teams = np.empty(16, dtype=np.int64)
                    wc_scores = np.empty(16, dtype=np.float64)
                    
                    for div_idx in range(n_divs):
                        if div_to_conf[div_idx] != conf_idx:
                            continue
                        
                        div_team_count = 0
                        div_teams_local = np.empty(16, dtype=np.int64)
                        div_scores = np.empty(16, dtype=np.float64)
                        
                        for t in range(n_teams):
                            if team_conf[t] == conf_idx and team_div[t] == div_idx:
                                div_teams_local[div_team_count] = t
                                div_scores[div_team_count] = sim_pts[t] * 10000 + sim_reg[t]
                                div_team_count += 1
                        
                        # Sort descending
                        for i in range(div_team_count):
                            for j in range(i + 1, div_team_count):
                                if div_scores[j] > div_scores[i]:
                                    div_scores[i], div_scores[j] = div_scores[j], div_scores[i]
                                    div_teams_local[i], div_teams_local[j] = div_teams_local[j], div_teams_local[i]
                        
                        # Teams ranked 4+ go to wildcard pool
                        for k in range(3, div_team_count):
                            wc_teams[wc_count] = div_teams_local[k]
                            wc_scores[wc_count] = div_scores[k]
                            wc_count += 1
                    
                    # Sort wildcard pool
                    for i in range(wc_count):
                        for j in range(i + 1, wc_count):
                            if wc_scores[j] > wc_scores[i]:
                                wc_scores[i], wc_scores[j] = wc_scores[j], wc_scores[i]
                                wc_teams[i], wc_teams[j] = wc_teams[j], wc_teams[i]
                    
                    # Top 2 wildcards qualify
                    for k in range(min(2, wc_count)):
                        playoff_counts[wc_teams[k]] += 1
    
    return playoff_counts


def _prepare_sim_data(teams, real_schedule=None):
    """Prepare arrays for the JIT simulator."""
    n_teams = len(teams)
    abbrev_to_idx = {t["teamAbbrev"]: i for i, t in enumerate(teams)}
    
    base_points = np.array([t["points"] for t in teams], dtype=np.float64)
    base_reg_wins = np.array([t.get("regulationWins", 0) for t in teams], dtype=np.float64)
    pts_pace = np.array([t.get("pointsPace", 0) for t in teams], dtype=np.float64)
    
    # Conference/division encoding
    conf_names = sorted(set(t["conference"] for t in teams))
    div_names = sorted(set(t["division"] for t in teams))
    team_conf = np.array([conf_names.index(t["conference"]) for t in teams], dtype=np.int32)
    team_div = np.array([div_names.index(t["division"]) for t in teams], dtype=np.int32)
    
    div_to_conf = np.zeros(len(div_names), dtype=np.int32)
    for t in teams:
        div_to_conf[div_names.index(t["division"])] = conf_names.index(t["conference"])
    
    # Build schedule
    if real_schedule:
        home_list = [abbrev_to_idx[h] for h, a in real_schedule if h in abbrev_to_idx and a in abbrev_to_idx]
        away_list = [abbrev_to_idx[a] for h, a in real_schedule if h in abbrev_to_idx and a in abbrev_to_idx]
    else:
        # Synthetic schedule
        home_list, away_list = [], []
        conferences = defaultdict(list)
        for t in teams:
            conferences[t["conference"]].append(t["teamAbbrev"])
        games_remaining = {t["teamAbbrev"]: t.get("gamesRemaining", 0) for t in teams}
        
        rng = np.random.default_rng()
        for conf_name, conf_abbrevs in conferences.items():
            abbrevs = list(conf_abbrevs)
            avg_remaining = sum(games_remaining[a] for a in abbrevs) // (2 * max(len(abbrevs), 1))
            for _ in range(max(1, avg_remaining)):
                rng.shuffle(abbrevs)
                for i in range(0, len(abbrevs) - 1, 2):
                    home_list.append(abbrev_to_idx[abbrevs[i]])
                    away_list.append(abbrev_to_idx[abbrevs[i + 1]])
        
        n_inter = len(home_list) // 5
        all_idxs = list(range(n_teams))
        for _ in range(n_inter):
            pair = rng.choice(all_idxs, 2, replace=False)
            home_list.append(pair[0])
            away_list.append(pair[1])
    
    home_idxs = np.array(home_list, dtype=np.int64)
    away_idxs = np.array(away_list, dtype=np.int64)
    
    # Pre-compute home win probabilities
    n_games = len(home_idxs)
    home_probs = np.empty(n_games, dtype=np.float64)
    for g in range(n_games):
        h_pace = pts_pace[home_idxs[g]]
        a_pace = pts_pace[away_idxs[g]]
        total = h_pace + a_pace
        if total > 0:
            strength = h_pace / total
            prob = np.clip(strength * HOME_WIN_PROB / 0.5, 0.3, 0.7)
        else:
            prob = HOME_WIN_PROB
        home_probs[g] = prob
    
    return {
        "base_points": base_points,
        "base_reg_wins": base_reg_wins,
        "home_idxs": home_idxs,
        "away_idxs": away_idxs,
        "home_probs": home_probs,
        "n_teams": n_teams,
        "n_games": n_games,
        "team_conf": team_conf,
        "team_div": team_div,
        "n_confs": len(conf_names),
        "n_divs": len(div_names),
        "div_to_conf": div_to_conf,
        "abbrev_to_idx": abbrev_to_idx,
        "conf_names": conf_names,
        "div_names": div_names,
    }


def run_simulations_np(teams, num_simulations=10000, real_schedule=None):
    """Run simulations using Numba JIT."""
    if not teams:
        return {}
    
    data = _prepare_sim_data(teams, real_schedule)
    
    playoff_counts = _simulate_batch(
        data["base_points"], data["base_reg_wins"],
        data["home_idxs"], data["away_idxs"],
        data["home_probs"], data["n_teams"], data["n_games"],
        num_simulations,
        data["team_conf"], data["team_div"],
        data["n_confs"], data["n_divs"], data["div_to_conf"]
    )
    
    results = {}
    for i, team in enumerate(teams):
        abbrev = team["teamAbbrev"]
        playoff_pct = (playoff_counts[i] / num_simulations) * 100
        clinched = bool(playoff_pct >= 99.5) or team.get("clinchIndicator", "") in ("x", "y", "z", "p")
        eliminated = bool(playoff_pct <= 0.5)
        results[abbrev] = {
            "playoff_pct": round(float(playoff_pct), 1),
            "clinched": bool(clinched),
            "eliminated": bool(eliminated),
            "sim_count": int(playoff_counts[i]),
        }
    
    return results


def run_simulations_with_forced_result(teams, forced_home_abbrev, forced_away_abbrev,
                                        forced_winner_abbrev, ot_game=False,
                                        num_simulations=1000, real_schedule=None):
    """Run sim with one game's result pre-applied."""
    modified_teams = []
    for team in teams:
        t = dict(team)
        abbrev = t["teamAbbrev"]
        if abbrev in (forced_home_abbrev, forced_away_abbrev):
            if abbrev == forced_winner_abbrev:
                t["points"] += 2
                if not ot_game:
                    t["regulationWins"] = t.get("regulationWins", 0) + 1
            else:
                if ot_game:
                    t["points"] += 1
            t["gamesRemaining"] = max(0, t.get("gamesRemaining", 0) - 1)
        modified_teams.append(t)
    
    sched = getattr(run_simulations_np, "_real_schedule", real_schedule)
    return run_simulations_np(modified_teams, num_simulations=num_simulations, real_schedule=sched)


def run_simulations_with_multiple_forced(teams, forced_outcomes, num_simulations=2000):
    """Run sim with multiple game results pre-applied."""
    modified_teams = [dict(t) for t in teams]
    abbrev_to_idx = {t["teamAbbrev"]: i for i, t in enumerate(modified_teams)}
    
    for outcome in forced_outcomes:
        if isinstance(outcome, dict):
            home = outcome["home"]
            away = outcome["away"]
            winner = outcome["winner"]
            ot_game = outcome.get("ot_game", False)
        else:
            home, away, winner = outcome[0], outcome[1], outcome[2]
            ot_game = outcome[3] if len(outcome) > 3 else False
        
        loser = away if winner == home else home
        
        for abbrev in [home, away]:
            if abbrev in abbrev_to_idx:
                idx = abbrev_to_idx[abbrev]
                modified_teams[idx]["gamesRemaining"] = max(
                    0, modified_teams[idx].get("gamesRemaining", 0) - 1
                )
        
        if winner in abbrev_to_idx:
            idx = abbrev_to_idx[winner]
            modified_teams[idx]["points"] += 2
            if not ot_game:
                modified_teams[idx]["regulationWins"] = (
                    modified_teams[idx].get("regulationWins", 0) + 1
                )
        
        if ot_game and loser in abbrev_to_idx:
            idx = abbrev_to_idx[loser]
            modified_teams[idx]["points"] += 1
    
    sched = getattr(run_simulations_np, "_real_schedule", None)
    return run_simulations_np(modified_teams, num_simulations=num_simulations, real_schedule=sched)


# Aliases
run_simulations = run_simulations_np


def get_division_leaders(teams):
    divisions = defaultdict(list)
    for team in teams:
        divisions[team["division"]].append(team)
    return {d: sorted(ts, key=lambda x: (x["points"], x["regulationWins"]), reverse=True) for d, ts in divisions.items()}


def get_conference_wildcards(teams, conference):
    conf_teams = [t for t in teams if t["conference"] == conference]
    divisions = defaultdict(list)
    for t in conf_teams:
        divisions[t["division"]].append(t)
    pool = []
    for d, ts in divisions.items():
        s = sorted(ts, key=lambda x: (x["points"], x["regulationWins"]), reverse=True)
        pool.extend(s[3:])
    pool.sort(key=lambda x: (x["points"], x["regulationWins"]), reverse=True)
    return pool[:2]
