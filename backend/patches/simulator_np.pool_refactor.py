"""
NumPy-vectorized Monte Carlo playoff simulator.
Runs thousands of simulations in bulk using array operations.

Playoff format:
  - Top 3 teams per division qualify
  - Top 2 remaining teams per conference (wildcards) qualify
  - 8 teams per conference, 16 total

Points: regulation win 2 / loss 0; overtime or shootout win 2 / loss 1
(the "loser point"). Regulation wins are tracked separately because ROW is
an NHL tiebreaker.

Design (2026-09-29): the season is simulated ONCE as a pool of N seasons
(per-sim final points, regulation wins, W/OTL/L records, playoff flags and
the outcome code of each of tonight's games). Everything the site shows is
derived from that pool: per-game scenarios by slicing on tonight's outcome,
best/worst cases by overriding the handful of tonight's-game columns a
scenario forces and re-ranking the standings. Nothing re-runs the season
per scenario any more (the old code ran a full season Monte Carlo per team
per case - 64 full seasons per pipeline run).
"""

import numpy as np
from collections import defaultdict

# Restored to 100k on 2026-09-29 once the per-scenario re-simulation was
# removed; the full 1344-game pool at 100k now costs seconds, not minutes.
SIM_COUNT = 100000


HOME_WIN_PROB = 0.54
OT_PROBABILITY = 0.24

_BATCH = 5000  # sims per batch; bounds peak memory (~200-300 MB at 1344 games)

# Outcome code of one game, used for tonight's games and forced results:
#   0 = home wins in regulation   1 = away wins in regulation
#   2 = home wins in OT/SO        3 = away wins in OT/SO
# Points and regulation-win credit per code (winner 2; OT/SO loser 1).
_HOME_PTS_BY_CODE = np.array([2.0, 0.0, 2.0, 1.0])
_AWAY_PTS_BY_CODE = np.array([0.0, 2.0, 1.0, 2.0])
_HOME_REG_BY_CODE = np.array([1.0, 0.0, 0.0, 0.0])
_AWAY_REG_BY_CODE = np.array([0.0, 1.0, 0.0, 0.0])

# Elo-based win probability
_elo_ratings_cache = None

def _load_elo_ratings():
    """Load Elo ratings from file (cached)."""
    global _elo_ratings_cache
    if _elo_ratings_cache is not None:
        return _elo_ratings_cache
    try:
        import elo_engine
        _elo_ratings_cache = elo_engine.get_elo_ratings()
        return _elo_ratings_cache
    except Exception:
        return None

def _elo_win_probs(home_idxs, away_idxs, elo_array, home_bonus=100):
    """
    Compute per-game home win probability from Elo ratings.
    Returns array of shape (n_games,) with probabilities clipped to [0.25, 0.75].
    """
    home_elo = elo_array[home_idxs] + home_bonus
    away_elo = elo_array[away_idxs]
    probs = 1.0 / (1.0 + np.power(10.0, (away_elo - home_elo) / 400.0))
    return np.clip(probs, 0.25, 0.75)


def build_remaining_schedule(teams):
    """
    Build a synthetic remaining schedule as arrays.
    Returns: home_indices, away_indices (arrays of team indices per game)
    Plus a team index lookup.
    """
    abbrev_to_idx = {t["teamAbbrev"]: i for i, t in enumerate(teams)}
    n_teams = len(teams)

    # Organize by conference
    conferences = defaultdict(list)
    for t in teams:
        conferences[t["conference"]].append(t["teamAbbrev"])

    games_remaining = {t["teamAbbrev"]: t.get("gamesRemaining", 0) for t in teams}
    pts_pace = {t["teamAbbrev"]: t.get("pointsPace", 0) for t in teams}

    # Build game pairs
    home_list = []
    away_list = []

    rng = np.random.default_rng()

    for conf_name, conf_abbrevs in conferences.items():
        abbrevs = list(conf_abbrevs)
        avg_remaining = sum(games_remaining[a] for a in abbrevs) // (2 * max(len(abbrevs), 1))

        for _ in range(max(1, avg_remaining)):
            rng.shuffle(abbrevs)
            for i in range(0, len(abbrevs) - 1, 2):
                h, a = abbrevs[i], abbrevs[i + 1]
                if games_remaining.get(h, 0) > 0 or games_remaining.get(a, 0) > 0:
                    home_list.append(abbrev_to_idx[h])
                    away_list.append(abbrev_to_idx[a])

    # Add ~20% inter-conference games
    all_idxs = list(range(n_teams))
    n_inter = len(home_list) // 5
    for _ in range(n_inter):
        pair = rng.choice(all_idxs, 2, replace=False)
        home_list.append(pair[0])
        away_list.append(pair[1])

    return np.array(home_list, dtype=np.int32), np.array(away_list, dtype=np.int32)


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------

def _team_structure(teams):
    """Conference/division bookkeeping shared by every entry point.
    Returns (conf_names, div_names, team_conf, team_div, div_to_conf, conf_div_structure)
    where conf_div_structure[conf_idx] is a list of team-index arrays, one per division."""
    conf_names = sorted(set(t["conference"] for t in teams))
    div_names  = sorted(set(t["division"] for t in teams))
    team_conf  = np.array([conf_names.index(t["conference"]) for t in teams], dtype=np.int32)
    team_div   = np.array([div_names.index(t["division"]) for t in teams], dtype=np.int32)
    div_to_conf = {}
    for t in teams:
        div_to_conf[div_names.index(t["division"])] = conf_names.index(t["conference"])
    conf_div_structure = []
    for conf_idx in range(len(conf_names)):
        conf_divs = [d for d, c in div_to_conf.items() if c == conf_idx]
        conf_div_structure.append([np.where((team_conf == conf_idx) & (team_div == d))[0] for d in conf_divs])
    return conf_names, div_names, team_conf, team_div, div_to_conf, conf_div_structure


def _home_win_prob_fn(teams, elo_ratings):
    """Return f(home_idxs, away_idxs) -> per-game home win probability."""
    if elo_ratings:
        elo_array = np.array([elo_ratings.get(t["teamAbbrev"], 1500) for t in teams], dtype=np.float64)
        return lambda h, a: _elo_win_probs(h, a, elo_array)
    pts_pace = np.array([t.get("pointsPace", 0) for t in teams], dtype=np.float64)
    def _pace(h, a):
        home_pace = pts_pace[h]
        away_pace = pts_pace[a]
        total = home_pace + away_pace
        home_strength = np.where(total > 0, home_pace / total, 0.5)
        return np.clip(home_strength * HOME_WIN_PROB / 0.5, 0.3, 0.7)
    return _pace


def _game_points(home_wins, goes_to_ot):
    """
    Points and regulation-win credit for every (sim, game) cell.
    Regulation: winner 2, loser 0.  OT/SO: winner 2, loser 1.
    Returns home_pts, away_pts, home_reg, away_reg (float32 - small exact integers - same shape as inputs).
    """
    # OT: winner gets 2, loser gets 1 (the extra loser point)
    home_pts = np.where(home_wins, 2, np.where(goes_to_ot, 1, 0)).astype(np.float32)
    away_pts = np.where(~home_wins, 2, np.where(goes_to_ot, 1, 0)).astype(np.float32)
    # OT loser gets 1 point
    home_pts = np.where(~home_wins & goes_to_ot, 1, home_pts)
    away_pts = np.where(home_wins & goes_to_ot, 1, away_pts)
    # Regulation wins
    home_reg = (home_wins & ~goes_to_ot).astype(np.float32)
    away_reg = (~home_wins & ~goes_to_ot).astype(np.float32)
    return home_pts, away_pts, home_reg, away_reg


def _incidence(home_idxs, away_idxs, n_teams):
    """Game-to-team incidence matrices H, A of shape (n_games, n_teams):
    H[g, home_idxs[g]] = 1 and A[g, away_idxs[g]] = 1. Summing a (sims, games)
    per-game quantity into (sims, teams) is then one matrix product."""
    n_games = len(home_idxs)
    H = np.zeros((n_games, n_teams), dtype=np.float32)
    A = np.zeros((n_games, n_teams), dtype=np.float32)
    H[np.arange(n_games), home_idxs] = 1.0
    A[np.arange(n_games), away_idxs] = 1.0
    return H, A


def _accumulate(per_game_home, per_game_away, H, A):
    """Sum per-game quantities into per-team totals.
    per_game_home/away: list of k arrays, each (b, n_games) -> returns list of k
    arrays (b, n_teams), float64. All values are small integers, so the float32
    product is exact regardless of BLAS summation order."""
    k = len(per_game_home)
    b = per_game_home[0].shape[0]
    hb = np.concatenate(per_game_home, axis=0).astype(np.float32, copy=False)
    ab = np.concatenate(per_game_away, axis=0).astype(np.float32, copy=False)
    acc = (hb @ H + ab @ A).astype(np.float64)
    return [acc[i * b:(i + 1) * b] for i in range(k)]


def _sim_batch(rng, b, home_idxs, away_idxs, hw_prob, H, A, base_points, base_reg_wins):
    """
    Simulate b seasons over the given schedule.
    Returns (sim_pts, sim_reg, wins, otl, losses, home_wins, goes_ot):
      sim_pts/sim_reg/wins/otl/losses are (b, n_teams) float64 totals
      home_wins/goes_ot are (b, n_games) bool per-game results
    """
    n_teams = H.shape[1]
    n_games = len(home_idxs)
    win_rolls = rng.random((b, n_games))
    ot_rolls  = rng.random((b, n_games))
    home_wins = win_rolls < hw_prob[np.newaxis, :]
    goes_ot   = ot_rolls  < OT_PROBABILITY
    del win_rolls, ot_rolls

    home_pts, away_pts, home_reg, away_reg = _game_points(home_wins, goes_ot)

    # Record tracking (OT winner still gets a W; OT loser gets an OTL):
    #   home wins reg: home W,   away L
    #   home wins OT:  home W,   away OTL
    #   away wins reg: away W,   home L
    #   away wins OT:  away W,   home OTL
    pts, reg, wins, otl = _accumulate(
        [home_pts, home_reg, home_wins, ~home_wins & goes_ot],
        [away_pts, away_reg, ~home_wins, home_wins & goes_ot],
        H, A,
    )
    games_per_team = H.sum(axis=0, dtype=np.float64) + A.sum(axis=0, dtype=np.float64)
    losses = games_per_team[np.newaxis, :] - wins - otl
    sim_pts = base_points[np.newaxis, :] + pts
    sim_reg = base_reg_wins[np.newaxis, :] + reg
    return sim_pts, sim_reg, wins, otl, losses, home_wins, goes_ot


def run_simulations_np(teams, num_simulations=10000, real_schedule=None, seed=None):
    """
    Vectorized Monte Carlo simulation.

    Returns dict of {teamAbbrev: {"playoff_pct": float, "clinched": bool, "eliminated": bool}}
    """
    if not teams:
        return {}

    n_teams = len(teams)
    abbrev_to_idx = {t["teamAbbrev"]: i for i, t in enumerate(teams)}

    # Team metadata arrays
    base_points = np.array([t["points"] for t in teams], dtype=np.float64)
    base_reg_wins = np.array([t.get("regulationWins", 0) for t in teams], dtype=np.float64)

    hw_prob_fn = _home_win_prob_fn(teams, _load_elo_ratings())
    conf_names, div_names, team_conf, team_div, div_to_conf, conf_div_structure = _team_structure(teams)

    rng = np.random.default_rng(seed)
    playoff_counts = np.zeros(n_teams, dtype=np.int64)

    # Use real schedule if provided, otherwise generate synthetic
    if real_schedule:
        # Convert real schedule to index arrays
        fixed_home = np.array([abbrev_to_idx[h] for h, a in real_schedule if h in abbrev_to_idx and a in abbrev_to_idx], dtype=np.int32)
        fixed_away = np.array([abbrev_to_idx[a] for h, a in real_schedule if h in abbrev_to_idx and a in abbrev_to_idx], dtype=np.int32)
        use_real = True
    else:
        use_real = False

    # We run simulations in batches for memory efficiency
    batch_size = min(num_simulations, _BATCH)
    n_batches = (num_simulations + batch_size - 1) // batch_size
    H = A = None

    for batch_idx in range(n_batches):
        current_batch = min(batch_size, num_simulations - batch_idx * batch_size)

        # Use real or synthetic schedule
        if use_real:
            home_idxs, away_idxs = fixed_home, fixed_away
        else:
            home_idxs, away_idxs = build_remaining_schedule(teams)
        n_games = len(home_idxs)

        if n_games == 0:
            qualifiers = _determine_playoffs_single(
                base_points, base_reg_wins, team_conf, team_div,
                conf_names, div_names, div_to_conf, n_teams
            )
            playoff_counts[qualifiers] += current_batch
            continue

        if H is None or not use_real:
            H, A = _incidence(home_idxs, away_idxs, n_teams)
        hw_prob = hw_prob_fn(home_idxs, away_idxs)

        sim_points, sim_reg = _sim_batch(rng, current_batch, home_idxs, away_idxs, hw_prob, H, A,
                                         base_points, base_reg_wins)[:2]

        # Vectorized playoff determination
        _determine_playoffs_batch(
            sim_points, sim_reg, conf_div_structure, playoff_counts, current_batch
        )

    # Compute results
    total_sims = num_simulations
    results = {}
    for i, team in enumerate(teams):
        abbrev = team["teamAbbrev"]
        playoff_pct = (playoff_counts[i] / total_sims) * 100
        clinched = playoff_pct >= 99.5 or team.get("clinchIndicator", "") in ("x", "y", "z", "p")
        eliminated = playoff_pct <= 0.05  # only truly eliminated if essentially 0 (< 1 in 2000 sims)
        results[abbrev] = {
            "playoff_pct": round(float(playoff_pct), 1),
            "clinched": bool(clinched),
            "eliminated": bool(eliminated),
            "sim_count": int(playoff_counts[i]),
        }

    return results


def _determine_playoffs_batch(sim_points, sim_reg, conf_div_structure, playoff_counts, n_sims):
    """
    Vectorized playoff determination for a batch of simulations.
    sim_points: (n_sims, n_teams)
    sim_reg: (n_sims, n_teams)
    conf_div_structure: list of [list of team_idx arrays per division] per conference
    playoff_counts: (n_teams,) accumulator
    """
    for conf_divs in conf_div_structure:
        # For each division, find top 3
        wildcard_all_idxs = []
        wildcard_all_pts = []
        wildcard_all_reg = []

        for div_team_idxs in conf_divs:
            if len(div_team_idxs) == 0:
                continue

            # Get points and reg wins for this division: (n_sims, n_div_teams)
            div_pts = sim_points[:, div_team_idxs]
            div_reg = sim_reg[:, div_team_idxs]

            # Sort by points desc, reg wins desc (for each sim independently)
            # Use a composite score to avoid per-sim loops
            # Score = points * 1000 + reg_wins (ensures points is primary sort)
            composite = div_pts * 10000 + div_reg

            # argsort descending for each sim
            sorted_local = np.argsort(-composite, axis=1)  # (n_sims, n_div_teams)

            # Top 3 qualify from division
            for rank in range(min(3, len(div_team_idxs))):
                local_idxs = sorted_local[:, rank]  # (n_sims,) local index within division
                global_idxs = div_team_idxs[local_idxs]  # (n_sims,) global team index
                np.add.at(playoff_counts, global_idxs, 1)

            # Remaining go to wildcard pool
            for rank in range(3, len(div_team_idxs)):
                local_idxs = sorted_local[:, rank]
                global_idxs = div_team_idxs[local_idxs]
                wildcard_all_idxs.append(global_idxs)
                # Store their points/reg for sorting
                # Need actual values per sim
                pts_vals = np.take_along_axis(div_pts, local_idxs[:, np.newaxis], axis=1).squeeze()
                reg_vals = np.take_along_axis(div_reg, local_idxs[:, np.newaxis], axis=1).squeeze()
                wildcard_all_pts.append(pts_vals)
                wildcard_all_reg.append(reg_vals)

        if not wildcard_all_idxs:
            continue

        # Stack wildcard candidates: (n_sims, n_wc_candidates)
        wc_idxs = np.stack(wildcard_all_idxs, axis=1)  # (n_sims, n_candidates)
        wc_pts = np.stack(wildcard_all_pts, axis=1)
        wc_reg = np.stack(wildcard_all_reg, axis=1)

        # Sort wildcards by points desc, reg wins desc
        wc_composite = wc_pts * 10000 + wc_reg
        wc_sorted = np.argsort(-wc_composite, axis=1)

        # Top 2 wildcards qualify
        for rank in range(min(2, wc_idxs.shape[1])):
            wc_local = wc_sorted[:, rank]  # (n_sims,)
            wc_global = np.take_along_axis(wc_idxs, wc_local[:, np.newaxis], axis=1).squeeze()
            np.add.at(playoff_counts, wc_global, 1)


def _determine_playoffs_single(points, reg_wins, team_conf, team_div,
                                conf_names, div_names, div_to_conf, n_teams):
    """
    Determine playoff qualifiers for a single simulation.
    Returns array of team indices that made playoffs.
    """
    qualifiers = []

    for conf_idx in range(len(conf_names)):
        # Get divisions in this conference
        conf_divs = [d for d, c in div_to_conf.items() if c == conf_idx]

        wildcard_pool = []

        for div_idx in conf_divs:
            # Teams in this division
            div_mask = (team_conf == conf_idx) & (team_div == div_idx)
            div_team_idxs = np.where(div_mask)[0]

            if len(div_team_idxs) == 0:
                continue

            # Sort by points desc, reg wins desc
            div_pts = points[div_team_idxs]
            div_reg = reg_wins[div_team_idxs]
            # Create sort key: primary = points (desc), secondary = reg_wins (desc)
            sort_order = np.lexsort((-div_reg, -div_pts))
            sorted_idxs = div_team_idxs[sort_order]

            # Top 3 qualify
            for j in range(min(3, len(sorted_idxs))):
                qualifiers.append(sorted_idxs[j])

            # Rest go to wildcard pool
            for j in range(3, len(sorted_idxs)):
                wildcard_pool.append(sorted_idxs[j])

        # Top 2 wildcards by points
        if wildcard_pool:
            wc_idxs = np.array(wildcard_pool)
            wc_pts = points[wc_idxs]
            wc_reg = reg_wins[wc_idxs]
            wc_order = np.lexsort((-wc_reg, -wc_pts))
            for j in range(min(2, len(wc_order))):
                qualifiers.append(wc_idxs[wc_order[j]])

    return np.array(qualifiers, dtype=np.int64)


def run_simulations_with_forced_result(teams, forced_home_abbrev, forced_away_abbrev,
                                        forced_winner_abbrev, ot_game=False,
                                        num_simulations=1000, real_schedule=None):
    """
    Run sim with one game's result pre-applied.
    """
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

    return run_simulations_np(modified_teams, num_simulations=num_simulations, real_schedule=getattr(run_simulations_np, "_real_schedule", None))


def run_simulations_with_multiple_forced(teams, forced_outcomes, num_simulations=2000, real_schedule=None):
    """
    Run sim with multiple game results pre-applied.
    forced_outcomes: list of dicts with {home, away, winner, ot_game}
    real_schedule: remaining schedule (list of (home, away) tuples) to use instead of synthetic.
                    IMPORTANT: pass the FUTURE schedule only (tomorrow+). Tonight's games
                    should be excluded since they're passed via forced_outcomes.

    Kept for external callers; the live pipeline no longer uses this for
    best/worst cases (see run_scenario_analysis_vectorized), which derives
    forced scenarios from the single simulation pool instead.
    """
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

    return run_simulations_np(modified_teams, num_simulations=num_simulations, real_schedule=real_schedule)


# ---------------------------------------------------------------------------
# The simulation pool
# ---------------------------------------------------------------------------

class _Pool:
    """One batch-built pool of simulated seasons.

    outcomes   (n_sims, n_tonight) int8   outcome code of each of tonight's games
    playoffs   (n_sims, n_teams)   bool   made the playoffs
    records    (n_sims, n_teams, 3) int8  [wins, otl, losses] over the simulated games
    final_pts  (n_sims, n_teams)   float32
    final_reg  (n_sims, n_teams)   float32
    """
    def __init__(self, n_sims, n_teams, n_tonight, tonight_home_idx, tonight_away_idx,
                 conf_div_structure, team_conf):
        self.n_sims = n_sims
        self.n_teams = n_teams
        self.n_tonight = n_tonight
        self.tonight_home_idx = tonight_home_idx
        self.tonight_away_idx = tonight_away_idx
        self.conf_div_structure = conf_div_structure
        self.team_conf = team_conf
        self.outcomes  = np.empty((n_sims, n_tonight), dtype=np.int8)
        self.playoffs  = np.zeros((n_sims, n_teams),   dtype=np.bool_)
        self.records   = np.zeros((n_sims, n_teams, 3), dtype=np.int8)
        self.final_pts = np.zeros((n_sims, n_teams),   dtype=np.float32)
        self.final_reg = np.zeros((n_sims, n_teams),   dtype=np.float32)

    @property
    def playoff_pct(self):
        return self.playoffs.mean(axis=0) * 100  # (n_teams,)


def _build_pool(teams, num_simulations, tonight_home_idx, tonight_away_idx,
                sched_home, sched_away, rng):
    """
    Simulate num_simulations seasons. Tonight's games (tonight_home_idx /
    tonight_away_idx, may be empty) occupy schedule columns 0..n_tonight-1.
    sched_home/sched_away: full schedule index arrays with tonight's games
    already prepended, or None to draw a synthetic schedule per batch.
    """
    n_teams = len(teams)
    n_tonight = len(tonight_home_idx)
    base_points   = np.array([t["points"] for t in teams], dtype=np.float64)
    base_reg_wins = np.array([t.get("regulationWins", 0) for t in teams], dtype=np.float64)
    hw_prob_fn = _home_win_prob_fn(teams, _load_elo_ratings())
    conf_names, div_names, team_conf, team_div, div_to_conf, conf_div_structure = _team_structure(teams)

    pool = _Pool(num_simulations, n_teams, n_tonight, tonight_home_idx, tonight_away_idx,
                 conf_div_structure, team_conf)
    use_real = sched_home is not None
    H = A = hw_prob = None

    sim_offset = 0
    while sim_offset < num_simulations:
        b = min(_BATCH, num_simulations - sim_offset)
        sl = slice(sim_offset, sim_offset + b)

        if use_real:
            home_idxs, away_idxs = sched_home, sched_away
        else:
            syn_home, syn_away = build_remaining_schedule(teams)
            # Prepend tonight's games so they're guaranteed at known positions
            home_idxs = np.concatenate([tonight_home_idx.astype(np.int32), syn_home])
            away_idxs = np.concatenate([tonight_away_idx.astype(np.int32), syn_away])

        n_games = len(home_idxs)
        if n_games == 0:
            sim_pts = np.tile(base_points, (b, 1))
            sim_reg = np.tile(base_reg_wins, (b, 1))
            pool.final_pts[sl] = sim_pts.astype(np.float32)
            pool.final_reg[sl] = sim_reg.astype(np.float32)
            _determine_playoffs_batch_flags(sim_pts, sim_reg, conf_div_structure, pool.playoffs, sim_offset, b)
            sim_offset += b
            continue

        if H is None or not use_real:
            H, A = _incidence(home_idxs, away_idxs, n_teams)
            hw_prob = hw_prob_fn(home_idxs, away_idxs)

        sim_pts, sim_reg, wins, otl, losses, home_wins, goes_ot = _sim_batch(
            rng, b, home_idxs, away_idxs, hw_prob, H, A, base_points, base_reg_wins)

        pool.records[sl, :, 0] = wins
        pool.records[sl, :, 1] = otl
        pool.records[sl, :, 2] = losses
        pool.final_pts[sl] = sim_pts.astype(np.float32)
        pool.final_reg[sl] = sim_reg.astype(np.float32)

        # Tonight's outcome codes: tonight's games are always columns 0..n_tonight-1
        if n_tonight:
            hw = home_wins[:, :n_tonight]
            ot = goes_ot[:, :n_tonight]
            pool.outcomes[sl] = np.where(hw & ~ot, 0,
                                np.where(~hw & ~ot, 1,
                                np.where(hw & ot, 2, 3))).astype(np.int8)

        _determine_playoffs_batch_flags(sim_pts, sim_reg, conf_div_structure, pool.playoffs, sim_offset, b)
        sim_offset += b

    return pool


def _scenario_playoff_pct(pool, team_idx, forced, unplayed_cols):
    """
    Playoff % for one team under a forced scenario, derived from the pool
    rather than re-simulated.

    forced:        list of (tonight column, outcome code) to impose
    unplayed_cols: tonight columns to treat as not played (both teams 0 pts)

    Only the forced/unplayed columns change, so the per-sim standings are the
    pool's final standings plus the point/ROW difference between the imposed
    outcome and the outcome that sim actually drew. Every other game keeps the
    pool's draw, which is exactly the distribution a fresh simulation with the
    forced results pre-applied would have. Standings are then re-ranked for
    the team's conference only.
    """
    pts = pool.final_pts.astype(np.float64)   # copies
    reg = pool.final_reg.astype(np.float64)
    for col, code in forced:
        cur = pool.outcomes[:, col]
        h, a = pool.tonight_home_idx[col], pool.tonight_away_idx[col]
        pts[:, h] += _HOME_PTS_BY_CODE[code] - _HOME_PTS_BY_CODE[cur]
        pts[:, a] += _AWAY_PTS_BY_CODE[code] - _AWAY_PTS_BY_CODE[cur]
        reg[:, h] += _HOME_REG_BY_CODE[code] - _HOME_REG_BY_CODE[cur]
        reg[:, a] += _AWAY_REG_BY_CODE[code] - _AWAY_REG_BY_CODE[cur]
    for col in unplayed_cols:
        cur = pool.outcomes[:, col]
        h, a = pool.tonight_home_idx[col], pool.tonight_away_idx[col]
        pts[:, h] -= _HOME_PTS_BY_CODE[cur]
        pts[:, a] -= _AWAY_PTS_BY_CODE[cur]
        reg[:, h] -= _HOME_REG_BY_CODE[cur]
        reg[:, a] -= _AWAY_REG_BY_CODE[cur]

    conf_idx = int(pool.team_conf[team_idx])
    flags = np.zeros((pool.n_sims, pool.n_teams), dtype=np.bool_)
    _determine_playoffs_batch_flags(pts, reg, [pool.conf_div_structure[conf_idx]], flags, 0, pool.n_sims)
    return float(flags[:, team_idx].mean() * 100)


def _what_if_rows(pool, team_idx, base_pts):
    """What If table: group sims by the team's (wins, otl, losses) record."""
    rec = pool.records[:, team_idx, :].astype(np.int64)
    key = rec[:, 0] * 10000 + rec[:, 1] * 100 + rec[:, 2]
    uniq, inv, counts = np.unique(key, return_inverse=True, return_counts=True)
    made = np.bincount(inv, weights=pool.playoffs[:, team_idx].astype(np.float64), minlength=len(uniq))
    rows = []
    for k, count, m in zip(uniq, counts, made):
        if count < 10:
            continue
        w, rem = divmod(int(k), 10000)
        otl, l = divmod(rem, 100)
        rows.append({
            "wins": w,
            "losses": l,
            "otl": otl,
            "final_points": float(base_pts + w * 2 + otl),
            "times": int(count),
            "made_playoffs": int(m),
            "playoff_pct": round(int(m) / int(count) * 100, 1),
        })
    rows.sort(key=lambda r: (-r["wins"], -r["otl"]))
    return rows


def _run_odds_and_whatif(teams, num_simulations=SIM_COUNT, real_schedule=None, seed=None):
    """
    Run a full vectorized sim with record tracking.
    Returns (vect_odds dict, what_if_data dict, seed_probs dict).
    Used when no games are active tonight.
    """
    if not teams:
        return {}, {}, {}
    _bw, _sc, vect_odds, what_if_data, seed_probs = run_scenario_analysis_vectorized(
        teams, [], num_simulations=num_simulations, real_schedule=real_schedule, seed=seed)
    return vect_odds, what_if_data, seed_probs


def _determine_playoffs_batch_flags(sim_points, sim_reg, conf_div_structure, out_flags, offset, n_sims):
    """
    Like _determine_playoffs_batch but writes bool flags into out_flags[offset:offset+n_sims, :].
    out_flags: (total_sims, n_teams) bool array
    """
    for conf_divs in conf_div_structure:
        wildcard_idxs_list = []
        wildcard_pts_list  = []
        wildcard_reg_list  = []

        for div_team_idxs in conf_divs:
            if len(div_team_idxs) == 0:
                continue

            div_pts = sim_points[:, div_team_idxs]  # (n_sims, n_div)
            div_reg = sim_reg[:, div_team_idxs]
            composite = div_pts * 10000 + div_reg
            sorted_local = np.argsort(-composite, axis=1)  # (n_sims, n_div)

            for rank in range(min(3, len(div_team_idxs))):
                local_idxs = sorted_local[:, rank]                    # (n_sims,)
                global_idxs = div_team_idxs[local_idxs]              # (n_sims,)
                # Mark as playoff qualifier for each sim
                row_idxs = np.arange(n_sims)
                out_flags[offset:offset + n_sims, :][row_idxs, global_idxs] = True

            for rank in range(3, len(div_team_idxs)):
                local_idxs  = sorted_local[:, rank]
                global_idxs = div_team_idxs[local_idxs]
                pts_vals = np.take_along_axis(div_pts, local_idxs[:, np.newaxis], axis=1).squeeze(axis=1)
                reg_vals = np.take_along_axis(div_reg, local_idxs[:, np.newaxis], axis=1).squeeze(axis=1)
                wildcard_idxs_list.append(global_idxs)
                wildcard_pts_list.append(pts_vals)
                wildcard_reg_list.append(reg_vals)

        if not wildcard_idxs_list:
            continue

        wc_idxs = np.stack(wildcard_idxs_list, axis=1)  # (n_sims, n_cands)
        wc_pts  = np.stack(wildcard_pts_list,  axis=1)
        wc_reg  = np.stack(wildcard_reg_list,  axis=1)
        wc_comp = wc_pts * 10000 + wc_reg
        wc_sorted = np.argsort(-wc_comp, axis=1)

        row_idxs = np.arange(n_sims)
        for rank in range(min(2, wc_idxs.shape[1])):
            wc_local  = wc_sorted[:, rank]
            wc_global = np.take_along_axis(wc_idxs, wc_local[:, np.newaxis], axis=1).squeeze(axis=1)
            out_flags[offset:offset + n_sims, :][row_idxs, wc_global] = True


def _playoff_pct_rough(team):
    """Get a rough playoff % for a team dict (handles nested or flat playoffOdds)."""
    po = team.get("playoffOdds")
    if isinstance(po, dict):
        return po.get("playoff_pct", 0.0)
    return float(po or 0.0)


def run_scenario_analysis_vectorized(teams, tonight_games, num_simulations=SIM_COUNT, real_schedule=None, seed=None):
    """
    Vectorized scenario analysis for tonight's games.

    One pool of num_simulations seasons is simulated with tonight's games in
    the first columns. Per-game scenarios group the pool by each game's
    drawn outcome; best/worst cases impose the favourable/unfavourable
    outcomes on those columns and re-rank the standings (see
    _scenario_playoff_pct). No season is ever simulated more than once.

    tonight_games: list of {"homeTeamAbbrev": str, "awayTeamAbbrev": str, "gameId": str/int}
    seed: optional RNG seed for reproducible runs (tests); None = fresh entropy.

    Returns:
        best_worst: dict of {abbrev: {"best": float, "worst": float, "has_game": bool}}
        team_scenarios: dict of {abbrev: [list of scenario dicts per tonight game]}
        vect_odds: {abbrev: playoff_pct}
        what_if_data: {abbrev: [rows]}
        seed_probs: {abbrev: {seed: pct}}
    """
    if not teams:
        return {}, {}, {}, {}, {}

    tonight_games = tonight_games or []
    n_teams = len(teams)
    abbrev_to_idx = {t["teamAbbrev"]: i for i, t in enumerate(teams)}
    all_abbrevs = [t["teamAbbrev"] for t in teams]
    conf_of = {t["teamAbbrev"]: t["conference"] for t in teams}

    # Filter tonight's games to those with valid teams
    valid_games = [g for g in tonight_games
                   if g["homeTeamAbbrev"] in abbrev_to_idx
                   and g["awayTeamAbbrev"] in abbrev_to_idx]
    n_tonight = len(valid_games)

    teams_playing = set()
    for g in valid_games:
        teams_playing.add(g["homeTeamAbbrev"])
        teams_playing.add(g["awayTeamAbbrev"])

    tonight_home_idx = np.array([abbrev_to_idx[g["homeTeamAbbrev"]] for g in valid_games], dtype=np.int32)
    tonight_away_idx = np.array([abbrev_to_idx[g["awayTeamAbbrev"]] for g in valid_games], dtype=np.int32)

    # Build schedule: tonight's games are ALWAYS prepended so we can match them.
    # real_schedule covers future games (tomorrow+); tonight's are separate.
    tonight_pairs_set = {(g["homeTeamAbbrev"], g["awayTeamAbbrev"]) for g in valid_games}
    if real_schedule:
        rs_filtered = [(h, a) for h, a in real_schedule
                       if h in abbrev_to_idx and a in abbrev_to_idx
                       and (h, a) not in tonight_pairs_set]
        sched_home = np.concatenate([tonight_home_idx,
                                     np.array([abbrev_to_idx[h] for h, a in rs_filtered], dtype=np.int32)])
        sched_away = np.concatenate([tonight_away_idx,
                                     np.array([abbrev_to_idx[a] for h, a in rs_filtered], dtype=np.int32)])
    else:
        sched_home = sched_away = None

    rng = np.random.default_rng(seed)
    pool = _build_pool(teams, num_simulations, tonight_home_idx, tonight_away_idx,
                       sched_home, sched_away, rng)
    all_playoffs = pool.playoffs
    all_outcomes = pool.outcomes
    conf_div_structure = pool.conf_div_structure

    # --- Build results ---

    # Overall playoff % per team across all sims (used as baseline for deltas)
    team_playoff_pct = pool.playoff_pct  # (n_teams,)
    vect_odds = {abbrev: round(float(team_playoff_pct[abbrev_to_idx[abbrev]]), 1) for abbrev in all_abbrevs}

    # Per-game scenarios: group sims by each game's outcome
    team_scenarios = {a: [] for a in all_abbrevs}

    for ti, game in enumerate(valid_games):
        home = game["homeTeamAbbrev"]
        away = game["awayTeamAbbrev"]
        game_id = str(game.get("gameId", f"{home}_{away}"))

        outcome_col = all_outcomes[:, ti]
        valid_mask = outcome_col >= 0

        # Determine the conference(s) of teams in this game
        game_confs = {conf_of.get(home), conf_of.get(away)} - {None}

        # Skip showing this game in scenarios for OTHER teams if both participants
        # are clinched or both are eliminated — it can't affect the playoff race
        home_pct = float(team_playoff_pct[abbrev_to_idx[home]])
        away_pct = float(team_playoff_pct[abbrev_to_idx[away]])
        both_clinched   = (home_pct >= 99.5 and away_pct >= 99.5)
        both_eliminated = (home_pct <= 0.05 and away_pct <= 0.05)
        game_irrelevant = both_clinched or both_eliminated

        # Outcome masks are shared by every team, so build them once per game.
        # Outcome codes: 0=home reg, 1=away reg, 2=home OT win, 3=away OT win
        outcome_masks = {label: valid_mask & (outcome_col == code)
                         for code, label in [(0, "home_reg"), (1, "away_reg"), (2, "home_ot"), (3, "away_ot")]}
        outcome_pcts = {label: (all_playoffs[mask].mean(axis=0) * 100 if mask.sum() > 50 else None)
                        for label, mask in outcome_masks.items()}

        for abbrev in all_abbrevs:
            team_idx = abbrev_to_idx[abbrev]

            # Skip if this team is in a different conference than both game teams
            # (cross-conference games can't affect Eastern/Western playoff races separately)
            is_own = abbrev in (home, away)
            if not is_own and conf_of[abbrev] not in game_confs:
                continue
            # Skip games between two clinched or two eliminated teams
            # (they can't affect anyone else's playoff odds)
            if not is_own and game_irrelevant:
                continue

            results_by_outcome = {label: (float(v[team_idx]) if v is not None else None)
                                  for label, v in outcome_pcts.items()}

            # Merge OT outcomes into a single "if_ot" by averaging home_ot and away_ot
            home_ot = results_by_outcome.get("home_ot")
            away_ot = results_by_outcome.get("away_ot")
            if home_ot is not None and away_ot is not None:
                if_ot_home_wins = home_ot   # home wins OT
                if_ot_away_wins = away_ot   # away wins OT
            else:
                if_ot_home_wins = home_ot or away_ot
                if_ot_away_wins = home_ot or away_ot

            baseline_pct = float(team_playoff_pct[team_idx])
            hr = results_by_outcome["home_reg"]
            ar = results_by_outcome["away_reg"]

            home_delta_reg = round(hr - baseline_pct, 1) if hr is not None else None
            away_delta_reg = round(ar - baseline_pct, 1) if ar is not None else None
            home_ot_delta  = round(if_ot_home_wins - baseline_pct, 1) if if_ot_home_wins is not None else None
            away_ot_delta  = round(if_ot_away_wins - baseline_pct, 1) if if_ot_away_wins is not None else None

            max_abs = max(
                abs(home_delta_reg) if home_delta_reg is not None else 0,
                abs(away_delta_reg) if away_delta_reg is not None else 0,
                abs(home_ot_delta)  if home_ot_delta  is not None else 0,
                abs(away_ot_delta)  if away_ot_delta  is not None else 0,
            )
            impact = "high" if max_abs >= 3 else ("medium" if max_abs >= 1 else "low")

            # Skip games with negligible impact (sim noise on irrelevant games)
            if max_abs < 0.3 and not is_own:
                continue

            team_scenarios[abbrev].append({
                "game_id": game_id,
                "home_team": home,
                "away_team": away,
                "home_team_name": game.get("homeTeamCommonName", home),
                "away_team_name": game.get("awayTeamCommonName", away),
                "is_own_game": is_own,
                "if_home_reg_win_pct":  round(hr, 1) if hr is not None else None,
                "if_away_reg_win_pct":  round(ar, 1) if ar is not None else None,
                "if_home_ot_win_pct":   round(if_ot_home_wins, 1) if if_ot_home_wins is not None else None,
                "if_away_ot_win_pct":   round(if_ot_away_wins, 1) if if_ot_away_wins is not None else None,
                # Legacy fields — home_wins = reg win for home, away_wins = reg win for away
                "if_home_wins_pct":     round(hr, 1) if hr is not None else None,
                "if_away_wins_pct":    round(ar, 1) if ar is not None else None,
                "home_delta_reg":  home_delta_reg,
                "away_delta_reg":  away_delta_reg,
                "home_ot_delta":   home_ot_delta,
                "away_ot_delta":   away_ot_delta,
                "home_delta":      home_delta_reg,
                "away_delta":      away_delta_reg,
                "impact":          impact,
            })

    # best/worst per team — forced scenario approach.
    # For each team, all favourable (or unfavourable) outcomes are imposed
    # simultaneously on the pool and the standings re-ranked. This gives the
    # compound probability without re-simulating.
    best_worst = {}

    def _is_rival_in_game(home, away, rivals):
        if home in rivals and away in rivals:
            return "both"
        if home in rivals:
            return home
        if away in rivals:
            return away
        return None

    def _build_forced_outcomes_for_scenario(valid_games, teams, abbrev_to_idx, team_playoff_pct, abbrev):
        """Build favorable and unfavorable forced_outcomes lists for a team."""
        team_idx = abbrev_to_idx[abbrev]
        team_conf_name = conf_of[abbrev]
        rivals = {
            t["teamAbbrev"] for t in teams
            if t["conference"] == team_conf_name
            and t["teamAbbrev"] in abbrev_to_idx
            and 0.0 <= float(team_playoff_pct[abbrev_to_idx[t["teamAbbrev"]]]) < 99.0
        }

        favorable_outcomes = []
        unfavorable_outcomes = []

        for game in valid_games:
            home = game["homeTeamAbbrev"]
            away = game["awayTeamAbbrev"]

            home_conf = conf_of.get(home)
            away_conf = conf_of.get(away)
            is_own = abbrev in (home, away)
            rival = _is_rival_in_game(home, away, rivals)

            if not is_own and team_conf_name not in {home_conf, away_conf}:
                continue

            # Determine favorable/unfavorable outcomes
            # 0=home reg win, 1=away reg win, 2=home OT win, 3=away OT win
            if is_own:
                if home == abbrev:
                    fav_winner, unfav_winner = home, away
                else:
                    fav_winner, unfav_winner = away, home
                # OT is less favorable than regulation win for the team we want to win
                # But for compound best/worst, we consider the best possible outcome
                # Best case: team wins (reg or OT) + rivals lose
                # Worst case: team loses + rivals win
                # For simplicity, use regulation wins for forced sim
                favorable_outcomes.append({"home": home, "away": away, "winner": fav_winner, "ot_game": False})
                unfavorable_outcomes.append({"home": home, "away": away, "winner": unfav_winner, "ot_game": False})
            elif rival == "both":
                # Both teams are rivals. Use per-game conditional playoff% for THIS team
                # to determine which winner actually helps/hurts most.
                game_id_str = str(game.get("gameId", f"{home}_{away}"))
                home_win_pct = None
                away_win_pct = None
                for sc in team_scenarios.get(abbrev, []):
                    if str(sc.get("game_id")) == game_id_str:
                        home_win_pct = sc.get("if_home_reg_win_pct")
                        away_win_pct = sc.get("if_away_reg_win_pct")
                        break
                if home_win_pct is not None and away_win_pct is not None:
                    # Best case: winner whose outcome gives THIS team the higher playoff%
                    if home_win_pct >= away_win_pct:
                        favorable_outcomes.append({"home": home, "away": away, "winner": home, "ot_game": False})
                        unfavorable_outcomes.append({"home": home, "away": away, "winner": away, "ot_game": False})
                    else:
                        favorable_outcomes.append({"home": home, "away": away, "winner": away, "ot_game": False})
                        unfavorable_outcomes.append({"home": home, "away": away, "winner": home, "ot_game": False})
                else:
                    # Fallback: if no scenario data, skip this game
                    continue
            elif rival == home:
                # Rival is home: their win hurts us → we want them to lose
                favorable_outcomes.append({"home": home, "away": away, "winner": away, "ot_game": False})
                unfavorable_outcomes.append({"home": home, "away": away, "winner": home, "ot_game": False})
            elif rival == away:
                # Rival is away: their win hurts us → we want them to lose
                favorable_outcomes.append({"home": home, "away": away, "winner": home, "ot_game": False})
                unfavorable_outcomes.append({"home": home, "away": away, "winner": away, "ot_game": False})
            else:
                # Neither is own team or rival — default: assume away win is favorable
                favorable_outcomes.append({"home": home, "away": away, "winner": away, "ot_game": False})
                unfavorable_outcomes.append({"home": home, "away": away, "winner": home, "ot_game": False})

        return favorable_outcomes, unfavorable_outcomes

    # tonight column of each (home, away) pair
    tonight_col = {(g["homeTeamAbbrev"], g["awayTeamAbbrev"]): ti for ti, g in enumerate(valid_games)}
    tonight_game_by_id = {str(g.get("gameId", f"{g['homeTeamAbbrev']}_{g['awayTeamAbbrev']}")): g
                          for g in valid_games}

    def _forced_to_cols(outcomes):
        """[{home, away, winner, ot_game}] -> [(tonight column, outcome code)]"""
        cols = []
        for o in outcomes:
            col = tonight_col[(o["home"], o["away"])]
            home_won = o["winner"] == o["home"]
            code = (2 if home_won else 3) if o.get("ot_game", False) else (0 if home_won else 1)
            cols.append((col, code))
        return cols

    for abbrev in all_abbrevs:
        team_idx = abbrev_to_idx[abbrev]
        baseline = round(float(team_playoff_pct[team_idx]), 1)

        if n_tonight == 0:
            best_worst[abbrev] = {"best": baseline, "worst": baseline, "has_game": False}
            continue

        # Games that appear in this team's scenario list (own game, or same-conference
        # games whose outcome moves this team by >= 0.3%)
        relevant_games = []
        for scenario in team_scenarios.get(abbrev, []):
            g = tonight_game_by_id.get(str(scenario.get("game_id")))
            if g is not None:
                relevant_games.append(g)

        if not relevant_games:
            best_worst[abbrev] = {"best": baseline, "worst": baseline, "has_game": abbrev in teams_playing}
            continue

        # Build favorable/unfavorable outcomes for relevant games only
        fav_outcomes, unfav_outcomes = _build_forced_outcomes_for_scenario(
            relevant_games, teams, abbrev_to_idx, team_playoff_pct, abbrev
        )

        # Tonight's games this scenario does not force are treated as not played,
        # which is what the previous forced re-simulation did (it dropped every
        # tonight game from the schedule and pre-applied only the forced ones).
        # Cross-conference games cannot affect this team either way; the only
        # games this touches are same-conference games below the 0.3% threshold.
        def _case(outcomes):
            if not outcomes:
                return baseline
            forced = _forced_to_cols(outcomes)
            forced_cols = {c for c, _ in forced}
            unplayed = [c for c in range(n_tonight) if c not in forced_cols]
            return _scenario_playoff_pct(pool, team_idx, forced, unplayed)

        best_pct = _case(fav_outcomes)
        worst_pct = _case(unfav_outcomes)

        # Monotonicity: by construction best >= baseline >= worst. All three now
        # come from the same pool (common random numbers), but the favourable
        # direction of a near-zero-impact game is chosen heuristically and can
        # be marginally wrong, which used to leak sub-0.5% inversions onto the
        # page. Clamp so the published trio is always ordered.
        baseline_raw = float(team_playoff_pct[team_idx])
        best_pct = max(best_pct, baseline_raw)
        worst_pct = min(worst_pct, baseline_raw)

        best_worst[abbrev] = {
            "best": round(best_pct, 1),
            "worst": round(worst_pct, 1),
            "has_game": abbrev in teams_playing,
        }

    # Build What If tables from record tracking
    # For each team: group sims by (wins, otl, losses), compute playoff%
    what_if_data = {}
    for abbrev in all_abbrevs:
        team_idx = abbrev_to_idx[abbrev]
        # Skip clinched/eliminated teams
        pct = float(team_playoff_pct[team_idx])
        if pct >= 99.5 or pct <= 0.05:
            continue
        what_if_data[abbrev] = _what_if_rows(pool, team_idx, teams[team_idx]["points"])

    # Seed probability distribution — per-team breakdown of how often they finish each seed
    seed_probs = _compute_seed_probs(pool.final_pts, pool.final_reg, teams, abbrev_to_idx,
                                     conf_div_structure, num_simulations, all_playoffs)

    return best_worst, team_scenarios, vect_odds, what_if_data, seed_probs


def _compute_seed_probs(all_final_pts, all_final_reg, teams, abbrev_to_idx, conf_div_structure, num_simulations, all_playoffs=None):
    """
    For each team, compute the probability of finishing at each conference seed (1-16).
    Uses all_playoffs to ensure seeds 1-8 sum exactly to playoff odds.
    Playoff teams get seeds 1-8 (ranked by points among playoff qualifiers).
    Non-playoff teams get seeds 9-16 (ranked by points among non-qualifiers).
    Returns {abbrev: {seed: pct, ...}}

    NHL seeding rules:
      - Seeds 1-2: Division winners (ranked by points)
      - Seeds 3-4: 2nd in each division (one per division, ranked by points)
      - Seeds 5-6: 3rd in each division (one per division, ranked by points)
      - Seeds 7-8: Wildcards (best remaining non-division-winners by points)
      - Seeds 9-16: Non-playoff teams (ranked by points)
    Fully vectorized across sims; ties in the composite keep team order
    (stable sorts), as the previous per-sim implementation did.
    """
    n_teams = len(teams)
    max_seed = 16
    seed_counts = np.zeros((n_teams, max_seed + 1), dtype=np.int64)  # index 0 unused
    NEG = -np.inf

    batch_size = 5000
    for offset in range(0, num_simulations, batch_size):
        b = min(batch_size, num_simulations - offset)
        rows = np.arange(b)
        pts_batch = all_final_pts[offset:offset + b].astype(np.float64)
        reg_batch = all_final_reg[offset:offset + b].astype(np.float64)
        composite = pts_batch * 10000 + reg_batch
        playoff_batch = all_playoffs[offset:offset + b] if all_playoffs is not None else None

        for conf_idx, divs in enumerate(conf_div_structure):
            conf_team_idxs = np.concatenate([div for div in divs])
            if len(conf_team_idxs) == 0:
                continue
            conf_comp = composite[:, conf_team_idxs]  # (b, n_conf_teams)
            n_conf = len(conf_team_idxs)

            if playoff_batch is None:
                # Fallback: simple conference ranking
                ranks = np.argsort(-conf_comp, axis=1)
                for local_rank in range(min(max_seed, n_conf)):
                    global_idxs = conf_team_idxs[ranks[:, local_rank]]
                    np.add.at(seed_counts[:, local_rank + 1], global_idxs, 1)
                continue

            conf_playoff = playoff_batch[:, conf_team_idxs]  # (b, n_conf_teams) bool
            global_to_local = {int(g): i for i, g in enumerate(conf_team_idxs)}
            in_comp = np.where(conf_playoff, conf_comp, NEG)   # non-playoff teams sort last

            # Position of each playoff team within its division (1st/2nd/3rd...)
            tier_local = [[], [], []]   # tier -> list of (b,) local idx arrays (one per division), -1 if none
            for div_global_idxs in divs:
                dl = np.array([global_to_local[int(g)] for g in div_global_idxs if int(g) in global_to_local])
                if len(dl) == 0:
                    continue
                div_in = in_comp[:, dl]                                   # (b, n_div)
                order = np.argsort(-div_in, axis=1, kind="stable")        # playoff teams first, by points
                n_in = conf_playoff[:, dl].sum(axis=1)                    # playoff teams in this division
                for tier in range(3):
                    if tier < len(dl):
                        li = dl[order[:, tier]]
                        tier_local[tier].append(np.where(n_in > tier, li, -1))

            seeded = np.zeros((b, n_conf), dtype=np.bool_)
            for tier, first_seed in ((0, 1), (1, 3), (2, 5)):
                if not tier_local[tier]:
                    continue
                cand = np.stack(tier_local[tier], axis=1)                 # (b, n_div)
                valid = cand >= 0
                cand_comp = np.where(valid, conf_comp[rows[:, None], np.where(valid, cand, 0)], NEG)
                order = np.argsort(-cand_comp, axis=1, kind="stable")
                for rank in range(min(2, cand.shape[1])):
                    pick = cand[rows, order[:, rank]]
                    ok = pick >= 0
                    np.add.at(seed_counts[:, first_seed + rank], conf_team_idxs[pick[ok]], 1)
                    seeded[rows[ok], pick[ok]] = True

            # Seeds 7-8: remaining playoff teams (wildcards), ranked by points
            rem_comp = np.where(conf_playoff & ~seeded, conf_comp, NEG)
            order = np.argsort(-rem_comp, axis=1, kind="stable")
            for rank in range(min(2, n_conf)):
                pick = order[:, rank]
                ok = rem_comp[rows, pick] > NEG
                np.add.at(seed_counts[:, 7 + rank], conf_team_idxs[pick[ok]], 1)

            # Seeds 9-16: non-playoff teams, ranked by points
            out_comp = np.where(conf_playoff, NEG, conf_comp)
            order = np.argsort(-out_comp, axis=1, kind="stable")
            for rank in range(min(max_seed - 8, n_conf)):
                pick = order[:, rank]
                ok = out_comp[rows, pick] > NEG
                np.add.at(seed_counts[:, 9 + rank], conf_team_idxs[pick[ok]], 1)

    seed_probs = {}
    for i, team in enumerate(teams):
        abbrev = team["teamAbbrev"]
        probs = {}
        for s in range(1, max_seed + 1):
            pct = round(seed_counts[i, s] / num_simulations * 100, 1)
            if pct >= 0.1:
                probs[s] = pct
        seed_probs[abbrev] = probs

    return seed_probs


# Aliases for backward compatibility
run_simulations = run_simulations_np


def get_division_leaders(teams):
    """Return current division standings."""
    divisions = defaultdict(list)
    for team in teams:
        divisions[team["division"]].append(team)
    result = {}
    for div_name, div_teams in divisions.items():
        sorted_teams = sorted(div_teams, key=lambda x: (x["points"], x["regulationWins"]), reverse=True)
        result[div_name] = sorted_teams
    return result


def get_conference_wildcards(teams, conference):
    """Return the 2 wildcard teams for a conference based on current standings."""
    conf_teams = [t for t in teams if t["conference"] == conference]
    divisions = defaultdict(list)
    for team in conf_teams:
        divisions[team["division"]].append(team)

    wildcard_pool = []
    for div_name, div_teams in divisions.items():
        sorted_div = sorted(div_teams, key=lambda x: (x["points"], x["regulationWins"]), reverse=True)
        wildcard_pool.extend(sorted_div[3:])

    wildcard_pool.sort(key=lambda x: (x["points"], x["regulationWins"]), reverse=True)
    return wildcard_pool[:2]
