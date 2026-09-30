"""
Regression tests for the pooled Monte Carlo simulator (simulator_np.py).

Self-contained: builds a synthetic 32-team league (2 conferences x 2 divisions)
and a random schedule, no network, no data files. Runs standalone or under pytest:

    cd backend && python3 tests/test_simulator_pool.py
    cd backend && python3 -m pytest tests/test_simulator_pool.py

Guards:
  1. NHL points identity: league points == 2 * games + OT games (the loser point)
  2. Regulation wins == games - OT games; W + OTL + L == games per team
  3. Vectorised accumulation == the per-game loop it replaced, exactly
  4. Outcome-code tables == the literal points formula for all four outcomes
  5. A forced OT result still awards the loser point (override arithmetic)
  6. Playoff odds sum to 1600 (16 of 32); best >= baseline >= worst; no NaNs
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import simulator_np as sim  # noqa: E402

if not hasattr(sim, "_build_pool"):
    print("simulator_np.py is the pre-pool version; nothing to test here")
    sys.exit(0)


def _league(seed=0):
    rng = np.random.default_rng(seed)
    teams = []
    for i in range(32):
        conf = "Eastern" if i < 16 else "Western"
        div = ["Atlantic", "Metropolitan", "Central", "Pacific"][i // 8]
        teams.append({"teamAbbrev": f"T{i:02d}", "conference": conf, "division": div,
                      "points": int(rng.integers(0, 20)), "regulationWins": int(rng.integers(0, 6)),
                      "gamesRemaining": 70, "pointsPace": 0})
    games = []
    for _ in range(35):                      # 35 rounds x 16 games = 560 games
        order = rng.permutation(32)
        for j in range(0, 32, 2):
            games.append((f"T{order[j]:02d}", f"T{order[j + 1]:02d}"))
    return teams, games


def _pool(teams, games, n=4000, n_tonight=3, seed=1):
    a2i = {t["teamAbbrev"]: i for i, t in enumerate(teams)}
    th = np.array([a2i[h] for h, a in games[:n_tonight]], np.int32)
    ta = np.array([a2i[a] for h, a in games[:n_tonight]], np.int32)
    sh = np.array([a2i[h] for h, a in games], np.int32)
    sa = np.array([a2i[a] for h, a in games], np.int32)
    return sim._build_pool(teams, n, th, ta, sh, sa, np.random.default_rng(seed)), sh, sa


def test_points_identity_and_records():
    teams, games = _league()
    sim._elo_ratings_cache = {t["teamAbbrev"]: 1500.0 for t in teams}
    a2i = {t["teamAbbrev"]: i for i, t in enumerate(teams)}
    home = np.array([a2i[h] for h, a in games], np.int32)
    away = np.array([a2i[a] for h, a in games], np.int32)
    base_pts = np.array([t["points"] for t in teams], float)
    base_reg = np.array([t["regulationWins"] for t in teams], float)
    H, A = sim._incidence(home, away, 32)
    hw_prob = sim._home_win_prob_fn(teams, sim._elo_ratings_cache)(home, away)
    b = 2000
    pts, reg, wins, otl, losses, home_wins, goes_ot = sim._sim_batch(
        np.random.default_rng(3), b, home, away, hw_prob, H, A, base_pts, base_reg)
    n_games = len(games)
    n_ot = goes_ot.sum(axis=1)
    # 1. loser point: every OT game puts 3 points into the league instead of 2
    assert np.array_equal((pts - base_pts).sum(axis=1), 2 * n_games + n_ot)
    # 2. regulation wins and W/OTL/L bookkeeping
    assert np.array_equal((reg - base_reg).sum(axis=1), n_games - n_ot)
    per_team = np.bincount(home, minlength=32) + np.bincount(away, minlength=32)
    assert np.array_equal(wins + otl + losses, np.tile(per_team, (b, 1)))
    assert np.array_equal(pts - base_pts, 2 * wins + otl)
    # OT share matches the constant
    assert abs(n_ot.mean() / n_games - sim.OT_PROBABILITY) < 0.01
    # 3. matches the per-game loop it replaced, exactly
    hp, ap, hr, ar = sim._game_points(home_wins, goes_ot)
    lp = np.tile(base_pts, (b, 1)); lr = np.tile(base_reg, (b, 1))
    for g in range(n_games):
        lp[:, home[g]] += hp[:, g]; lp[:, away[g]] += ap[:, g]
        lr[:, home[g]] += hr[:, g]; lr[:, away[g]] += ar[:, g]
    assert np.array_equal(lp, pts) and np.array_equal(lr, reg)


def test_outcome_code_tables_match_formula():
    hw = np.array([True, False, True, False]); ot = np.array([False, False, True, True])
    code = np.array([0, 1, 2, 3])
    hp, ap, hr, ar = sim._game_points(hw, ot)
    assert (hp == sim._HOME_PTS_BY_CODE[code]).all() and (ap == sim._AWAY_PTS_BY_CODE[code]).all()
    assert (hr == sim._HOME_REG_BY_CODE[code]).all() and (ar == sim._AWAY_REG_BY_CODE[code]).all()
    assert list(hp) == [2, 0, 2, 1] and list(ap) == [0, 2, 1, 2]      # OT loser gets 1
    assert list(hr) == [1, 0, 0, 0] and list(ar) == [0, 1, 0, 0]      # only regulation wins count as ROW


def test_forced_ot_override_awards_loser_point():
    teams, games = _league()
    sim._elo_ratings_cache = {t["teamAbbrev"]: 1500.0 for t in teams}
    pool, sh, sa = _pool(teams, games, n=1500, n_tonight=1)
    h, a = int(pool.tonight_home_idx[0]), int(pool.tonight_away_idx[0])
    cur = pool.outcomes[:, 0]
    # take out what the pool drew for game 0, put in "away wins in OT" (code 3)
    home_after = pool.final_pts[:, h] - sim._HOME_PTS_BY_CODE[cur] + sim._HOME_PTS_BY_CODE[3]
    away_after = pool.final_pts[:, a] - sim._AWAY_PTS_BY_CODE[cur] + sim._AWAY_PTS_BY_CODE[3]
    home_rest = pool.final_pts[:, h] - sim._HOME_PTS_BY_CODE[cur]
    away_rest = pool.final_pts[:, a] - sim._AWAY_PTS_BY_CODE[cur]
    assert np.array_equal(home_after - home_rest, np.full(len(cur), 1.0))   # OT loser: 1
    assert np.array_equal(away_after - away_rest, np.full(len(cur), 2.0))   # OT winner: 2


def test_scenario_outputs_are_sane_and_monotone():
    teams, games = _league()
    sim._elo_ratings_cache = {t["teamAbbrev"]: 1500.0 + 40 * np.sin(i) for i, t in enumerate(teams)}
    tonight = [{"homeTeamAbbrev": h, "awayTeamAbbrev": a, "gameId": f"g{i}"} for i, (h, a) in enumerate(games[:4])]
    bw, sc, odds, wif, seeds = sim.run_scenario_analysis_vectorized(
        teams, tonight, num_simulations=6000, real_schedule=games[4:], seed=7)
    assert abs(sum(odds.values()) - 1600) < 0.5
    assert all(0 <= v <= 100 for v in odds.values())
    for ab, v in bw.items():
        assert v["worst"] <= odds[ab] + 0.05 <= v["best"] + 0.1, (ab, v, odds[ab])
        assert v["best"] >= v["worst"]
    assert len(seeds) == 32 and all(abs(sum(p for s, p in seeds[ab].items() if s <= 8) - odds[ab]) < 0.5 for ab in odds)
    base = {t["teamAbbrev"]: t["points"] for t in teams}
    for ab, rows in wif.items():
        assert rows, ab
        for r in rows:
            assert r["final_points"] == base[ab] + 2 * r["wins"] + r["otl"], (ab, r)
            assert r["times"] >= 10 and 0 <= r["made_playoffs"] <= r["times"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"ok  {name}")
    print("ALL TESTS PASSED")
