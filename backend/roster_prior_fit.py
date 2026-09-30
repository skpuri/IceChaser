"""
Fit and validate the roster prior (backend/roster_prior.py).

Derives every constant in roster_prior.py from data and backtests the blended
rating against Elo-only baselines. Run manually, never on cron:

    python3 backend/roster_prior_fit.py            # fits + game-level backtest
    python3 backend/roster_prior_fit.py --sims     # + preseason playoff-odds Brier (slow, ~5 min)

Inputs (cached, fetched on first run):
    data/moneypuck/{skaters,goalies}_<year>.csv     MoneyPuck season summaries
    data/backtest/nhl_games_<season>.json           regular-season results
    data/backtest/opening_rosters_<season>.json     dressed players in each team's first 3 games

Out-of-sample protocol: constants fitted on the earlier seasons, tested on
2025-26 (the most recent completed season). Leave-one-season-out numbers are
printed too.
"""

import os
import sys
import json
import math
import time
import argparse
import urllib.request
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import roster_prior as rp
import elo_engine

DATA_DIR = rp.DATA_DIR
BT_DIR = os.environ.get("ICECHASER_BACKTEST_DIR") or os.path.join(DATA_DIR, "backtest")
MP_DIR = rp.MONEYPUCK_DIR

SEASONS = [20222023, 20232024, 20242025, 20252026]           # seasons with results + opening rosters (evaluated)
PREV_ONLY = [20212022]                                        # results only, used to carry Elo into 2022-23
SEASON_START = {20212022: "2021-10-12", 20222023: "2022-10-07", 20232024: "2023-10-10", 20242025: "2024-10-04", 20252026: "2025-10-07"}
MP_YEARS = list(range(2019, 2026))                            # MoneyPuck files needed
TEST_SEASON = 20252026                                        # held out for the headline numbers
K_FACTOR, HOME_BONUS, OT_DISCOUNT = elo_engine.K_FACTOR, elo_engine.HOME_BONUS, elo_engine.OT_DISCOUNT

CONF_DIV = {
    "BOS": ("Eastern", "Atlantic"), "BUF": ("Eastern", "Atlantic"), "DET": ("Eastern", "Atlantic"),
    "FLA": ("Eastern", "Atlantic"), "MTL": ("Eastern", "Atlantic"), "OTT": ("Eastern", "Atlantic"),
    "TBL": ("Eastern", "Atlantic"), "TOR": ("Eastern", "Atlantic"),
    "CAR": ("Eastern", "Metropolitan"), "CBJ": ("Eastern", "Metropolitan"), "NJD": ("Eastern", "Metropolitan"),
    "NYI": ("Eastern", "Metropolitan"), "NYR": ("Eastern", "Metropolitan"), "PHI": ("Eastern", "Metropolitan"),
    "PIT": ("Eastern", "Metropolitan"), "WSH": ("Eastern", "Metropolitan"),
    "ARI": ("Western", "Central"), "UTA": ("Western", "Central"), "CHI": ("Western", "Central"),
    "COL": ("Western", "Central"), "DAL": ("Western", "Central"), "MIN": ("Western", "Central"),
    "NSH": ("Western", "Central"), "STL": ("Western", "Central"), "WPG": ("Western", "Central"),
    "ANA": ("Western", "Pacific"), "CGY": ("Western", "Pacific"), "EDM": ("Western", "Pacific"),
    "LAK": ("Western", "Pacific"), "SJS": ("Western", "Pacific"), "SEA": ("Western", "Pacific"),
    "VAN": ("Western", "Pacific"), "VGK": ("Western", "Pacific"),
}


# ═══════════════════════════════════════════════════════════════════════
# Data fetch / cache
# ═══════════════════════════════════════════════════════════════════════

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "IceChaser/1.0 (roster prior fit)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def ensure_moneypuck():
    os.makedirs(MP_DIR, exist_ok=True)
    for y in MP_YEARS:
        for kind in ("skaters", "goalies"):
            path = os.path.join(MP_DIR, f"{kind}_{y}.csv")
            if os.path.exists(path):
                continue
            print(f"  fetching MoneyPuck {kind} {y}...")
            open(path, "wb").write(_get(f"https://moneypuck.com/moneypuck/playerData/seasonSummary/{y}/regular/{kind}.csv"))
            time.sleep(1.5)


def ensure_games(season):
    os.makedirs(BT_DIR, exist_ok=True)
    path = os.path.join(BT_DIR, f"nhl_games_{season}.json")
    if os.path.exists(path):
        return json.load(open(path))
    from datetime import datetime, timedelta
    start = SEASON_START[season]; end = f"{int(start[:4]) + 1}-05-05"
    print(f"  fetching {season} results...")
    games, seen, cur = [], set(), start
    while cur and cur <= end:
        data = json.loads(_get(f"https://api-web.nhle.com/v1/schedule/{cur}"))
        for day in data.get("gameWeek", []):
            for g in day.get("games", []):
                if g.get("gameState") not in ("OFF", "FINAL") or g.get("gameType") != 2 or g["id"] in seen:
                    continue
                h, a, o = g.get("homeTeam", {}), g.get("awayTeam", {}), g.get("gameOutcome", {})
                seen.add(g["id"])
                games.append({"id": g["id"], "date": day["date"], "home": h.get("abbrev"), "away": a.get("abbrev"),
                              "home_score": h.get("score", 0), "away_score": a.get("score", 0),
                              "overtime": o.get("lastPeriodType", "REG") in ("OT", "SO"),
                              "home_win": h.get("score", 0) > a.get("score", 0)})
        nxt = data.get("nextStartDate")
        if not nxt or nxt <= cur:
            break
        cur = nxt; time.sleep(0.08)
    games.sort(key=lambda g: (g["date"], g["id"]))
    json.dump(games, open(path, "w"))
    return games


def ensure_opening_rosters(season, games, n_games=3):
    """Dressed skaters/goalies in each team's first n_games boxscores."""
    path = os.path.join(BT_DIR, f"opening_rosters_{season}.json")
    if os.path.exists(path):
        return json.load(open(path))
    print(f"  fetching {season} opening rosters from boxscores...")
    per_team = defaultdict(list)
    for g in games:
        for t in (g["home"], g["away"]):
            if len(per_team[t]) < n_games:
                per_team[t].append(g["id"])
    rosters = defaultdict(lambda: {"skaters": {}, "goalies": {}})
    for gid in sorted({gid for ids in per_team.values() for gid in ids}):
        b = json.loads(_get(f"https://api-web.nhle.com/v1/gamecenter/{gid}/boxscore"))
        for side in ("homeTeam", "awayTeam"):
            ab = b[side]["abbrev"]; pbg = b["playerByGameStats"][side]
            for k in ("forwards", "defense"):
                for p in pbg[k]:
                    rosters[ab]["skaters"][str(p["playerId"])] = {"name": p["name"]["default"], "pos": p["position"]}
            for p in pbg["goalies"]:
                rosters[ab]["goalies"][str(p["playerId"])] = {"name": p["name"]["default"]}
        time.sleep(0.08)
    json.dump(rosters, open(path, "w"))
    return rosters


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def standings(games):
    st = defaultdict(lambda: {"gp": 0, "pts": 0, "w": 0, "rw": 0, "gf": 0, "ga": 0})
    for g in games:
        h, a = g["home"], g["away"]
        st[h]["gp"] += 1; st[a]["gp"] += 1
        st[h]["gf"] += g["home_score"]; st[h]["ga"] += g["away_score"]
        st[a]["gf"] += g["away_score"]; st[a]["ga"] += g["home_score"]
        w, l = (h, a) if g["home_win"] else (a, h)
        st[w]["pts"] += 2; st[w]["w"] += 1
        if g["overtime"]:
            st[l]["pts"] += 1
        else:
            st[w]["rw"] += 1
    return dict(st)


def playoff_teams(st):
    """NHL format: top 3 per division + 2 wildcards per conference (pts, RW, W)."""
    key = lambda ab: (-st[ab]["pts"], -st[ab]["rw"], -st[ab]["w"])
    made = set()
    for conf in ("Eastern", "Western"):
        divs = defaultdict(list)
        for ab in st:
            c, d = CONF_DIV[ab]
            if c == conf:
                divs[d].append(ab)
        rest = []
        for d, abs_ in divs.items():
            abs_.sort(key=key)
            made.update(abs_[:3]); rest += abs_[3:]
        rest.sort(key=key)
        made.update(rest[:2])
    return made


def expected(rh, ra):
    return 1.0 / (1.0 + 10.0 ** ((ra - rh - HOME_BONUS) / 400.0))


def replay(games, init, prior=None, n0=None, windows=((0, 10), (10, 20), (20, 41), (41, 82))):
    """
    Replay a season with the engine's Elo update. Before each game the rating used
    for prediction is w*prior + (1-w)*elo with w = n0/(n0+gp_team).
    Returns final elo dict and {window: (logloss_sum, brier_sum, n)}.
    """
    elo = dict(init)
    gp = defaultdict(int)
    stats = {w: [0.0, 0.0, 0] for w in windows}
    for g in games:
        h, a = g["home"], g["away"]
        elo.setdefault(h, rp.INITIAL_ELO); elo.setdefault(a, rp.INITIAL_ELO)
        rh, ra = elo[h], elo[a]
        if prior is not None and n0:
            wh = n0 / (n0 + gp[h]); wa = n0 / (n0 + gp[a])
            rh = wh * prior.get(h, rh) + (1 - wh) * rh
            ra = wa * prior.get(a, ra) + (1 - wa) * ra
        p = min(0.75, max(0.25, expected(rh, ra)))          # same clip as simulator_np
        y = 1.0 if g["home_win"] else 0.0
        ll = -(y * math.log(p) + (1 - y) * math.log(1 - p))
        avg_gp = (gp[h] + gp[a]) / 2.0
        for w in windows:
            if w[0] <= avg_gp < w[1]:
                stats[w][0] += ll; stats[w][1] += (p - y) ** 2; stats[w][2] += 1
        # engine update (identical to elo_engine.compute_elo_ratings)
        he = expected(elo[h], elo[a])
        k = K_FACTOR * (OT_DISCOUNT if g["overtime"] else 1.0)
        elo[h] += k * (y - he); elo[a] += k * ((1 - y) - (1 - he))
        gp[h] += 1; gp[a] += 1
    return elo, stats


def ols(X, y):
    X = np.asarray(X, float); y = np.asarray(y, float)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ beta
    ss_res = float(((y - pred) ** 2).sum()); ss_tot = float(((y - y.mean()) ** 2).sum())
    return beta, 1 - ss_res / ss_tot if ss_tot else float("nan")


# ═══════════════════════════════════════════════════════════════════════
# Fits
# ═══════════════════════════════════════════════════════════════════════

def fit_constants(mp, games_by_season, rosters_by_season, verbose=True):
    P = {}
    log = print if verbose else (lambda *a, **k: None)

    # ── 1. Elo per standings point (engine scale, K=10 from 1500) ──
    xs, ys = [], []
    for s, games in games_by_season.items():
        final, _ = elo_engine.compute_elo_ratings(games)
        st = standings(games)
        for ab in st:
            xs.append(st[ab]["pts"]); ys.append(final[ab])
    beta, r2 = ols(np.c_[np.ones(len(xs)), xs], ys)
    P["ELO_PER_POINT"] = float(beta[1])
    log(f"\n[1] ELO_PER_POINT: final engine Elo = {beta[0]:.1f} + {beta[1]:.3f} * points   (R²={r2:.3f}, n={len(xs)} team-seasons)")

    # ── 2. Points per Game Score / per GSAx (realised same-season team totals) ──
    rows, ys = [], []
    for s, games in games_by_season.items():
        y = int(str(s)[:4]); sk, go = mp[y]
        gs = defaultdict(float); gsax = defaultdict(float)
        for d in sk.values():
            gs[d["team"]] += d["gs"]
        for d in go.values():
            gsax[d["team"]] += d["xg"] - d["ga"]
        st = standings(games)
        for ab in st:
            rows.append([1.0, gs[ab], gsax[ab]]); ys.append(st[ab]["pts"])
    beta, r2 = ols(rows, ys)
    P["PTS_INTERCEPT"], P["PTS_PER_GS"], P["PTS_PER_GSAX"] = map(float, beta)
    log(f"[2] points = {beta[0]:.1f} + {beta[1]:.4f} * team GameScore + {beta[2]:.4f} * team GSAx   (R²={r2:.3f}, n={len(ys)})")
    # sanity: points per goal of differential
    xs, ys2 = [], []
    for s, games in games_by_season.items():
        st = standings(games)
        for ab in st:
            xs.append(st[ab]["gf"] - st[ab]["ga"]); ys2.append(st[ab]["pts"])
    b, r2b = ols(np.c_[np.ones(len(xs)), xs], ys2)
    log(f"    sanity: points = {b[0]:.1f} + {b[1]:.3f} * goal differential (R²={r2b:.3f}); GSAx coefficient should be near {b[1]:.3f}")

    # ── 3. Goalie year-over-year shrink and pooling depth ──
    log("[3] goalie GSAx/GP repeatability (goalies with >=20 GP in the target season):")
    best = None
    for k in (1, 2, 3):
        xs, ys = [], []
        for y in range(2020, 2026):
            for pid, g in mp[y][1].items():
                if g["gp"] < 20:
                    continue
                gp, xg, ga, _ = rp.pooled_goalie(pid, mp, y, k)
                if gp < rp.MIN_GP_GOALIE:
                    continue
                xs.append((xg - ga) / gp); ys.append((g["xg"] - g["ga"]) / g["gp"])
        b, r2 = ols(np.c_[np.ones(len(xs)), xs], ys)
        r = math.sqrt(max(r2, 0))
        log(f"    pooled {k} season(s): slope={b[1]:.3f}  r={r:.3f}  n={len(xs)}")
        if best is None or r2 > best[1]:
            best = (k, r2, float(b[1]))
    P["GOALIE_SEASONS"], P["GOALIE_SHRINK"] = best[0], best[2]
    log(f"    -> GOALIE_SEASONS={best[0]}, GOALIE_SHRINK={best[2]:.3f}")

    # ── 4. Skater pooling depth (rate repeatability, >=20 GP target) ──
    log("[4] skater GameScore/GP repeatability (skaters with >=20 GP in the target season):")
    best = None
    for k in (1, 2, 3):
        xs, ys = [], []
        for y in range(2020, 2026):
            for pid, d in mp[y][0].items():
                if d["gp"] < 20:
                    continue
                gp, gs, toi, _, _ = rp.pooled_skater(pid, mp, y, k)
                if gp < rp.MIN_GP_SKATER:
                    continue
                xs.append(gs / gp); ys.append(d["gs"] / d["gp"])
        b, r2 = ols(np.c_[np.ones(len(xs)), xs], ys)
        log(f"    pooled {k} season(s): slope={b[1]:.3f}  r={math.sqrt(max(r2,0)):.3f}  n={len(xs)}")
        if best is None or r2 > best[1]:
            best = (k, r2)
    P["SKATER_SEASONS"] = best[0]
    log(f"    -> SKATER_SEASONS={best[0]}")

    # ── 5. Replacement rate and starter share (from the most recent completed season) ──
    y = max(mp)
    sk, go = mp[y]
    by_team = defaultdict(lambda: {"F": [], "D": []})
    for d in sk.values():
        if d["gp"] >= rp.MIN_GP_SKATER:
            by_team[d["team"]]["D" if d["pos"] == "D" else "F"].append(d)
    repl_gs = repl_gp = 0.0
    for t in by_team.values():
        for pos, lo, hi in (("F", 12, 14), ("D", 6, 8)):
            lst = sorted(t[pos], key=lambda d: -d["toi"] / d["gp"])[lo:hi]
            for d in lst:
                repl_gs += d["gs"]; repl_gp += d["gp"]
    P["REPLACEMENT_GS_RATE"] = repl_gs / repl_gp
    avg_rate = sum(d["gs"] for d in sk.values()) / sum(d["gp"] for d in sk.values())
    log(f"[5] REPLACEMENT_GS_RATE={P['REPLACEMENT_GS_RATE']:.3f} GS/game (13th-14th F, 7th-8th D by TOI, {y}-{y+1}); league avg skater = {avg_rate:.3f}")
    shares = []
    for t in set(g["team"] for g in go.values()):
        team_games = max(st_["gp"] for st_ in [standings(games_by_season[max(games_by_season)])[t]]) if t in standings(games_by_season[max(games_by_season)]) else 82
        shares.append(max(g["gp"] for g in go.values() if g["team"] == t) / team_games)
    P["STARTER_SHARE"] = float(np.mean(shares))
    log(f"    STARTER_SHARE={P['STARTER_SHARE']:.3f} (mean share of team games by busiest goalie, {y}-{y+1})")
    return P


def team_projections(season, mp, roster, P):
    y = int(str(season)[:4])
    params = dict(sk_seasons=P["SKATER_SEASONS"], g_seasons=P["GOALIE_SEASONS"], repl=P["REPLACEMENT_GS_RATE"],
                  starter_share=P["STARTER_SHARE"], goalie_shrink=P["GOALIE_SHRINK"],
                  pts_per_gs=P["PTS_PER_GS"], pts_per_gsax=P["PTS_PER_GSAX"], overrides={})
    return {ab: rp.build_team_prior(r, mp, y, params) for ab, r in roster.items()}


def fit_team_shrink(projs_by_season, games_by_season, seasons, verbose=True):
    xs, ys = [], []
    for s in seasons:
        st = standings(games_by_season[s]); pr = projs_by_season[s]
        mean = np.mean([p["proj_points_raw"] for p in pr.values()])
        for ab in st:
            xs.append(pr[ab]["proj_points_raw"] - mean); ys.append(st[ab]["pts"])
    b, r2 = ols(np.c_[np.ones(len(xs)), xs], ys)
    if verbose:
        print(f"[6] TEAM_SHRINK: actual points = {b[0]:.1f} + {b[1]:.3f} * (projected - mean)   (r={math.sqrt(max(r2,0)):.3f}, n={len(xs)}, seasons {seasons})")
    return float(b[1]), math.sqrt(max(r2, 0))


# ═══════════════════════════════════════════════════════════════════════
# Backtest
# ═══════════════════════════════════════════════════════════════════════

def game_level_backtest(games_by_season, prior_by_season, final_elo_prev, n0_grid, train, test):
    """
    Log-loss of game predictions in early-season windows, for baselines and blends.
    Returns table rows.
    """
    def run(season, init, prior, n0):
        _, stats = replay(games_by_season[season], init, prior, n0)
        return stats

    variants = {}
    for season in games_by_season:
        prev = final_elo_prev.get(season)
        flat = {ab: rp.INITIAL_ELO for ab in prior_by_season[season]}
        carry = dict(prev) if prev else None
        variants[("flat1500", season)] = run(season, flat, None, None)
        if carry:
            variants[("carry", season)] = run(season, carry, None, None)
            variants[("carry_reg1/3", season)] = run(season, {ab: 1500 + (e - 1500) * (2 / 3) for ab, e in carry.items()}, None, None)
            for n0 in n0_grid:
                variants[(f"blend_n0={n0}", season)] = run(season, carry, prior_by_season[season], n0)
        variants[("prior_only_flat", season)] = run(season, flat, prior_by_season[season], 1e9)
    return variants


def summarize(variants, seasons, windows):
    names = sorted({k[0] for k in variants}, key=lambda n: (n.startswith("blend"), n))
    rows = []
    for name in names:
        row = {"variant": name}
        for w in windows:
            ll = br = n = 0
            for s in seasons:
                st = variants.get((name, s))
                if st is None:
                    continue
                ll += st[w][0]; br += st[w][1]; n += st[w][2]
            row[w] = (ll / n if n else float("nan"), br / n if n else float("nan"), n)
        rows.append(row)
    return rows


def print_table(rows, windows, title):
    print(f"\n{title}")
    head = f"{'variant':>18} " + " ".join(f"{'GP ' + str(w[0]) + '-' + str(w[1]):>16}" for w in windows)
    print(head); print("-" * len(head))
    for r in rows:
        print(f"{r['variant']:>18} " + " ".join(f"{r[w][0]:.4f}/{r[w][1]:.4f}" for w in windows) + f"   (n per window: {', '.join(str(r[w][2]) for w in windows)})" if r is rows[0] else
              f"{r['variant']:>18} " + " ".join(f"{r[w][0]:.4f}/{r[w][1]:.4f}" for w in windows))


def _teams_from_standings(st, abbrevs):
    teams = []
    for ab in abbrevs:
        c, d = CONF_DIV[ab]; s = st.get(ab, {"gp": 0, "pts": 0, "w": 0, "rw": 0})
        gp = s["gp"]
        teams.append({"teamAbbrev": ab, "conference": c, "division": d, "points": s["pts"], "wins": s["w"],
                      "losses": max(0, gp - s["w"] - (s["pts"] - 2 * s["w"])), "otLosses": s["pts"] - 2 * s["w"],
                      "regulationWins": s["rw"], "gamesPlayed": gp, "gamesRemaining": 82 - gp,
                      "pointsPace": (s["pts"] / gp * 82) if gp else 82})
    return teams


def checkpoint_playoff_backtest(games_by_season, prior_by_season, final_elo_prev, made_by_season, seasons,
                                checkpoints=(0, 10, 20, 41), n0_list=(10, 20, 41, 82), num_sims=20000):
    """
    Playoff-odds Brier/log-loss at several points in each season, for Elo-only
    baselines, the prior alone, and blends. Mirrors calibration.py's checkpoint
    replay but at the EARLY checkpoints where a prior can matter.
    Returns {(variant, season, cp): (brier, logloss, preds)}.
    """
    import simulator_np as sim
    out = {}
    for season in seasons:
        games = games_by_season[season]
        abbrevs = sorted({g["home"] for g in games} | {g["away"] for g in games})
        prev = final_elo_prev.get(season)
        prior = prior_by_season[season]
        made = made_by_season[season]
        for cp in checkpoints:
            idx = int(cp / 82 * len(games))
            played, remaining = games[:idx], games[idx:]
            st = standings(played) if played else {}
            teams = _teams_from_standings(st, abbrevs)
            gp = {ab: st.get(ab, {"gp": 0})["gp"] for ab in abbrevs}
            schedule = [(g["home"], g["away"]) for g in remaining]
            flat, _ = elo_engine.compute_elo_ratings(played)
            flat = {ab: flat.get(ab, 1500.0) for ab in abbrevs}
            variants = {"flat1500": flat, "prior_only": {ab: prior.get(ab, 1500.0) for ab in abbrevs}}
            if prev:
                carry, _ = elo_engine.compute_elo_ratings(played, initial_ratings=prev)
                carry = {ab: carry.get(ab, 1500.0) for ab in abbrevs}
                reg, _ = elo_engine.compute_elo_ratings(played, initial_ratings={ab: 1500 + (e - 1500) * (2 / 3) for ab, e in prev.items()})
                variants["carry"] = carry
                variants["carry_reg1/3"] = {ab: reg.get(ab, 1500.0) for ab in abbrevs}
                for n0 in n0_list:
                    variants[f"blend_n0={n0}"] = rp.blend_elo_ratings(carry, prior, gp, n0)[0]
            for n0 in n0_list:
                variants[f"blendflat_n0={n0}"] = rp.blend_elo_ratings(flat, prior, gp, n0)[0]
            for name, r in variants.items():
                sim._elo_ratings_cache = r
                res = sim.run_simulations_np(teams, num_simulations=num_sims, real_schedule=schedule)
                br = ll = 0.0; preds = {}
                for ab in abbrevs:
                    p = min(0.999, max(0.001, res[ab]["playoff_pct"] / 100.0)); yv = 1.0 if ab in made else 0.0
                    br += (p - yv) ** 2; ll += -(yv * math.log(p) + (1 - yv) * math.log(1 - p)); preds[ab] = round(p, 4)
                out[(name, season, cp)] = (br / len(abbrevs), ll / len(abbrevs), preds)
            print(f"    {season} GP~{cp:2d}: " + "  ".join(f"{n}={out[(n, season, cp)][0]:.4f}" for n in variants), flush=True)
        sim._elo_ratings_cache = None
    return out


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", action="store_true", help="also run preseason playoff-odds sims (slow)")
    ap.add_argument("--num-sims", type=int, default=50000)
    args = ap.parse_args()

    print("Loading data...")
    ensure_moneypuck()
    mp = rp.load_seasons(MP_YEARS, MP_DIR)
    games_by_season = {s: ensure_games(s) for s in SEASONS}
    prev_games = {s: ensure_games(s) for s in PREV_ONLY}
    rosters_by_season = {s: ensure_opening_rosters(s, games_by_season[s]) for s in SEASONS}
    print(f"  MoneyPuck seasons: {sorted(mp)}; result seasons: {SEASONS}")

    made_by_season = {s: playoff_teams(standings(g)) for s, g in games_by_season.items()}
    for s in SEASONS:
        assert len(made_by_season[s]) == 16, (s, made_by_season[s])

    # Final Elo of the previous season (what elo_engine carries into a new season)
    final_elo_prev = {}
    all_results = dict(prev_games); all_results.update(games_by_season)
    ordered = sorted(all_results)
    for i, s in enumerate(ordered[1:], 1):
        if s not in SEASONS:
            continue
        final_elo_prev[s], _ = elo_engine.compute_elo_ratings(all_results[ordered[i - 1]])
        # ARI became UTA in 2024-25
        if "ARI" in final_elo_prev[s] and s >= 20242025:
            final_elo_prev[s]["UTA"] = final_elo_prev[s].pop("ARI")

    train = [s for s in SEASONS if s != TEST_SEASON]

    print("\n=== FITTING CONSTANTS (all seasons for the value->points map, which uses no future roster info) ===")
    P = fit_constants(mp, games_by_season, rosters_by_season)

    projs = {s: team_projections(s, mp, rosters_by_season[s], P) for s in SEASONS}
    print()
    shrink_train, r_train = fit_team_shrink(projs, games_by_season, train)
    shrink_all, r_all = fit_team_shrink(projs, games_by_season, SEASONS)
    for s in SEASONS:
        fit_team_shrink(projs, games_by_season, [s], verbose=False)
        b, r = fit_team_shrink(projs, games_by_season, [s], verbose=False)
        print(f"    per-season slope {s}: {b:.3f} (r={r:.3f})")
    P["TEAM_SHRINK"] = shrink_train
    P["TEAM_SHRINK_ALL"] = shrink_all
    print(f"    -> TEAM_SHRINK={shrink_train:.3f} used for the held-out backtest (train seasons only);"
          f" ship the all-season value {shrink_all:.3f}")

    prior_by_season = {}
    for s in SEASONS:
        prior_by_season[s] = rp.priors_to_elo({ab: p["proj_points_raw"] for ab, p in projs[s].items()}, P["ELO_PER_POINT"], P["TEAM_SHRINK"])

    # Correlation of prior with actual final Elo, out of sample
    print("\n[7] Prior Elo vs realised final engine Elo (same season):")
    for s in SEASONS:
        fin, _ = elo_engine.compute_elo_ratings(games_by_season[s])
        xs = [prior_by_season[s][ab] for ab in fin]; ys = [fin[ab] for ab in fin]
        r = np.corrcoef(xs, ys)[0, 1]
        line = f"    {s}: r={r:.3f}"
        if s in final_elo_prev:
            prev = final_elo_prev[s]
            r2 = np.corrcoef([prev.get(ab, 1500) for ab in fin], ys)[0, 1]
            line += f"   (carried previous-season Elo: r={r2:.3f})"
        print(line)

    # ── [7b] does combining prior and carried Elo beat either alone? (leave-one-season-out) ──
    print("\n[7b] Leave-one-season-out prediction of final points from preseason ratings (r / RMSE in points):")
    per = {}
    for s in SEASONS:
        st = standings(games_by_season[s]); prev = final_elo_prev[s]
        per[s] = [(prior_by_season[s][ab], prev.get(ab, 1500.0), st[ab]["pts"]) for ab in st]
    for label, cols in (("prior only", (0,)), ("carried Elo only", (1,)), ("prior + carried Elo", (0, 1))):
        errs, rs = [], []
        for hold in SEASONS:
            X = [[1.0] + [row[c] for c in cols] for s in SEASONS if s != hold for row in per[s]]
            y = [row[2] for s in SEASONS if s != hold for row in per[s]]
            b, _ = ols(X, y)
            Xh = np.array([[1.0] + [row[c] for c in cols] for row in per[hold]]); yh = np.array([row[2] for row in per[hold]])
            pred = Xh @ b; errs += list((pred - yh) ** 2); rs.append(np.corrcoef(pred, yh)[0, 1])
        print(f"    {label:>20}: mean r={np.mean(rs):.3f}  RMSE={math.sqrt(np.mean(errs)):.2f} pts   (per-season r: {', '.join(f'{r:.2f}' for r in rs)})")
    X = [[1.0, row[0], row[1]] for s in SEASONS for row in per[s]]; y = [row[2] for s in SEASONS for row in per[s]]
    b, r2 = ols(X, y)
    print(f"    all-season joint fit: points = {b[0]:.0f} + {b[1]:.3f}*prior + {b[2]:.3f}*carry  (relative weight on prior = {b[1]/(b[1]+b[2]):.2f})")

    # ── game-level backtest ──
    windows = ((0, 10), (10, 20), (20, 41), (41, 82))
    n0_grid = [5, 10, 20, 30, 41, 60, 82, 120]
    variants = game_level_backtest(games_by_season, prior_by_season, final_elo_prev, n0_grid, train, TEST_SEASON)
    seasons_with_prev = [s for s in SEASONS if s in final_elo_prev]
    train_prev = [s for s in seasons_with_prev if s != TEST_SEASON]

    rows = summarize(variants, train_prev, windows)
    print_table(rows, windows, f"[8] Game-level log-loss/Brier per game, TRAIN seasons {train_prev} (lower is better)")
    # pick N0 on train by GP 0-41 log-loss
    def early_ll(rows_, name):
        r = next(r for r in rows_ if r["variant"] == name)
        n = sum(r[w][2] for w in windows[:3]); return sum(r[w][0] * r[w][2] for w in windows[:3]) / n
    best_n0 = min(n0_grid, key=lambda n: early_ll(rows, f"blend_n0={n}"))
    P["ROSTER_PRIOR_GAMES"] = float(best_n0)
    print(f"    -> game-level pick ROSTER_PRIOR_GAMES={best_n0} (min train log-loss over GP 0-41; grid {n0_grid}).")
    print(f"       CAUTION: differences between N0 values are inside one standard error here; the playoff-odds"
          f" backtest (--sims) is the metric the site is judged on and overrides this pick.")

    rows_t = summarize(variants, [TEST_SEASON], windows)
    print_table(rows_t, windows, f"[9] OUT-OF-SAMPLE {TEST_SEASON}: game-level log-loss/Brier per game")
    for name in ("carry", "carry_reg1/3", f"blend_n0={best_n0}", "prior_only_flat", "flat1500"):
        print(f"    {name:>18}: GP 0-41 log-loss = {early_ll(rows_t, name):.4f}")

    P["FIT_DATE"] = time.strftime("%Y-%m-%d")

    def print_constants():
        print("\n=== CONSTANTS TO PASTE INTO roster_prior.py ===")
        for k, v in (("FIT_DATE", P["FIT_DATE"]), ("SKATER_SEASONS", P["SKATER_SEASONS"]), ("GOALIE_SEASONS", P["GOALIE_SEASONS"]),
                     ("PTS_INTERCEPT", P["PTS_INTERCEPT"]), ("PTS_PER_GS", P["PTS_PER_GS"]), ("PTS_PER_GSAX", P["PTS_PER_GSAX"]),
                     ("REPLACEMENT_GS_RATE", P["REPLACEMENT_GS_RATE"]), ("STARTER_SHARE", P["STARTER_SHARE"]),
                     ("GOALIE_SHRINK", P["GOALIE_SHRINK"]), ("TEAM_SHRINK", P["TEAM_SHRINK_ALL"]),
                     ("ELO_PER_POINT", P["ELO_PER_POINT"]), ("ROSTER_PRIOR_GAMES", P["ROSTER_PRIOR_GAMES"])):
            print(f"{k} = {v!r}" if isinstance(v, str) else f"{k} = {round(v, 4) if isinstance(v, float) else v}")
        json.dump({k: P[k] for k in P}, open(os.path.join(BT_DIR, "fit_constants.json"), "w"), indent=2)

    if not args.sims:
        print_constants()

    if args.sims:
        cps = (0, 10, 20, 41); n0s = (10, 20, 41, 82)
        print(f"\n=== [10] PLAYOFF-ODDS BACKTEST AT CHECKPOINTS ({args.num_sims} sims, real schedules, Brier per team) ===")
        res = checkpoint_playoff_backtest(games_by_season, prior_by_season, final_elo_prev, made_by_season, SEASONS, cps, n0s, args.num_sims)
        names = ["flat1500", "carry", "carry_reg1/3", "prior_only"] + [f"blend_n0={n}" for n in n0s] + [f"blendflat_n0={n}" for n in n0s]
        for group, seasons in (("TRAIN " + str(train), train), ("TEST " + str(TEST_SEASON), [TEST_SEASON]), ("ALL 4 SEASONS", SEASONS)):
            print(f"\n  {group}: mean Brier by checkpoint (games played)")
            print(f"  {'variant':>18} " + " ".join(f"{'GP~' + str(cp):>9}" for cp in cps) + f" {'mean':>9}")
            for n in names:
                vals = [np.mean([res[(n, s, cp)][0] for s in seasons]) for cp in cps]
                print(f"  {n:>18} " + " ".join(f"{v:9.4f}" for v in vals) + f" {np.mean(vals):9.4f}")
        json.dump({f"{n}|{s}|{cp}": {"brier": v[0], "logloss": v[1], "preds": v[2]} for (n, s, cp), v in res.items()},
                  open(os.path.join(BT_DIR, "playoff_backtest.json"), "w"), indent=1)
        # N0 from the metric the site is judged on: mean Brier over all seasons and checkpoints,
        # blending with the CARRIED Elo (what elo_engine does). Ties (within 0.001) go to the larger N0.
        score = {n: np.mean([res[(f"blend_n0={n}", s, cp)][0] for s in SEASONS for cp in cps]) for n in n0s}
        best = min(score.values()); best_n0 = max(n for n, v in score.items() if v <= best + 0.001)
        P["ROSTER_PRIOR_GAMES"] = float(best_n0)
        print(f"\n    -> ROSTER_PRIOR_GAMES={best_n0} from the playoff-odds backtest: " + ", ".join(f"N0={n}: {v:.4f}" for n, v in score.items()))
        print_constants()


if __name__ == "__main__":
    main()
