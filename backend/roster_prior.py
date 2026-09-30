"""
IceChaser roster-aware team-strength prior.

WHY THIS EXISTS
---------------
elo_engine.py only knows game results. At 0 games played it carries last
season's final Elo forward, so a team that lost its franchise centre in July
and a team that signed one look identical. This module builds a prior from the
players actually on each roster today, and generate_data_v3.py blends it with
the game-derived Elo, weighted by how much hockey has been played.

Same shape as BaseChaser's backend/war_prior.py: a per-team prior table
(data/roster_prior.json, produced by generate_roster_prior.py), a documented
refresh procedure, and a blend into the existing Elo.

DATA SOURCE (why MoneyPuck)
---------------------------
Per-player season summaries from MoneyPuck (free CSV, no auth):
  https://moneypuck.com/moneypuck/playerData/seasonSummary/<year>/regular/skaters.csv
  https://moneypuck.com/moneypuck/playerData/seasonSummary/<year>/regular/goalies.csv
where <year> is the season's start year (2025 == 2025-26).

  * Skaters: `gameScore` -- Dom Luszczyszyn's Game Score (Hockey Graphs, 2016),
    a published box-score composite (goals, primary/secondary assists, shots,
    blocks, penalties, faceoffs, Corsi, on-ice goals). Used as the skater value
    unit. It is NOT a WAR, so it is converted to standings points by a
    regression fitted on realised team seasons (see PTS_PER_GS below).
  * Goalies: `xGoals` and `goals` (against) -- goals saved above expected
    (GSAx = xGoals - goals) is the standard public goaltending metric.

Why not the alternatives:
  * Evolving-Hockey GAR/WAR and The Athletic net ratings: paywalled, not
    fetchable from this host.
  * Natural Stat Trick: 403s this host.
  * Hockey-Reference point shares: fetchable, but keyed on HR player ids and
    names. MoneyPuck rows carry the NHL API `playerId`, so roster joins are
    exact (no name matching, no silent mis-attribution on a live site).
    Point shares are also constructed to sum to the team's actual points, so
    they carry less roster-transferable information than a box-score composite.

ROSTERS: NHL API https://api-web.nhle.com/v1/roster/<TEAM>/<season>
(current season only -- for past seasons that endpoint returns partial data,
so the backtest in roster_prior_fit.py uses boxscore dressed players instead).

MODEL
-----
For the season being projected (start year Y):
  1. Skater rate  = Game Score per game, pooled over seasons Y-1..Y-SKATER_SEASONS
     (needs >= MIN_GP_SKATER games, else the player is "unknown").
  2. Depth chart  = top N_FORWARDS forwards + N_DEFENSE defensemen on the
     roster by pooled ice time per game. Every slot gets 82 games. Unknown
     players (rookies, camp extras) only fill a slot if there are not enough
     known players, and are valued at REPLACEMENT_GS_RATE.
  3. Goalie rate  = GSAx per game pooled over Y-1..Y-GOALIE_SEASONS, shrunk
     by GOALIE_SHRINK (goalie results barely repeat year to year). The
     starter (most pooled games) gets STARTER_SHARE of the 82 starts, the
     backup the rest. This is the explicit goaltending handling: the single
     most valuable player on most rosters is the starter, so his identity is
     read from the roster, his value is regressed hard, and a
     GOALIE_OVERRIDES table lets the owner remove an injured starter.
  4. Projected points = PTS_PER_GS * skater GS + PTS_PER_GSAX * goalie GSAx
     (+ intercept, which cancels on centring).
  5. Prior Elo = INITIAL_ELO + ELO_PER_POINT * TEAM_SHRINK * (proj - league mean),
     clipped to [ELO_MIN, ELO_MAX].
  6. Blend: rating = w * prior + (1 - w) * game Elo,
     w = ROSTER_PRIOR_GAMES / (ROSTER_PRIOR_GAMES + games_played).
     Pure prior at 0 GP, 50/50 at ROSTER_PRIOR_GAMES, fades smoothly after.

Every constant below was fitted by backend/roster_prior_fit.py on cached
historical data (2019-20 .. 2025-26 MoneyPuck, 2022-23 .. 2025-26 results).
Re-run that script and paste its output here if you change the model.

REFRESH PROCEDURE
-----------------
  python3 backend/generate_roster_prior.py          # ~10 s, 32 roster calls
Run it after any trade / signing / long-term injury you care about, and at
least weekly in season. generate_data_v3.py also calls it automatically when
data/roster_prior.json is older than PRIOR_MAX_AGE_HOURS; if that fails it
uses the stale file, and if there is no file at all it falls back to pure Elo
(the behaviour the site had before this module existed).

Last constants fit: see FIT_DATE below.
"""

import json
import os
import math

# ── Files ──
DATA_DIR = os.environ.get("ICECHASER_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
PRIOR_FILE = os.path.join(DATA_DIR, "roster_prior.json")
MONEYPUCK_DIR = os.path.join(DATA_DIR, "moneypuck")
PRIOR_MAX_AGE_HOURS = 24

# ── Master switch (set to False to get the pre-prior behaviour back) ──
ENABLED = True

# ── Depth chart ──
N_FORWARDS = 12          # NHL dressed lineup: 12 F + 6 D + 2 G
N_DEFENSE = 6
SKATER_SEASONS = 2       # seasons pooled for skater rates (fit: see roster_prior_fit.py)
GOALIE_SEASONS = 3       # seasons pooled for goalie rates (fit: see roster_prior_fit.py)
MIN_GP_SKATER = 10       # below this a skater is "unknown" and valued at replacement
MIN_GP_GOALIE = 10       # below this a goalie is "unknown" and valued as league average (0 GSAx)

# ── Fitted constants (roster_prior_fit.py, run 2026-09-29 on 2019-20..2025-26
#    MoneyPuck files and 2021-22..2025-26 results; full log in data/backtest/) ──
FIT_DATE = "2026-09-29"
PTS_INTERCEPT = 52.1     # points = 52.1 + PTS_PER_GS*teamGS + PTS_PER_GSAX*teamGSAx, R²=0.79, n=128 team-seasons
PTS_PER_GS = 0.0542      # standings points per unit of team Game Score (same regression)
PTS_PER_GSAX = 0.2768    # standings points per goal saved above expected (same regression;
                         # sanity: points-per-goal-of-differential fitted separately = 0.311)
REPLACEMENT_GS_RATE = 0.193  # GS/game of the 13th-14th F and 7th-8th D by ice time, 2025-26 (league avg skater 0.465)
STARTER_SHARE = 0.607    # mean share of team games played by each team's busiest goalie, 2025-26
GOALIE_SHRINK = 0.298    # slope of next-season GSAx/GP on 3-season pooled GSAx/GP, goalies >=20 GP (r=0.25, n=334)
                         # 1-season pooled: slope 0.16 r=0.17; 2-season: 0.24/0.21; so 3 seasons pooled.
TEAM_SHRINK = 1.172      # slope of actual points on (projected - mean), all 4 seasons, n=128, r=0.62.
                         # >1 because the lineup projection compresses spreads (goalie regression,
                         # 18 fixed slots); it is a scale, not a shrink. Per-season slopes ran
                         # 1.53/1.45/0.80/0.76 (2022-23..2025-26): the two recent seasons were far
                         # less predictable. Train-only (first 3) value 1.27 was used in the backtest.
ELO_PER_POINT = 3.234    # final engine Elo (K=10, from 1500) = 1205 + 3.234*points, R²=0.97, n=128
ROSTER_PRIOR_GAMES = 41.0  # THE blend constant. Playoff-odds Brier (20k sims, real schedules) at
                         # GP 0/10/20/41 averaged over 4 seasons: N0=20 -> 0.1522, N0=41 -> 0.1522,
                         # N0=10 -> 0.1535, N0=82 -> 0.1527; carried Elo alone 0.1645, flat 1500
                         # 0.1752. 20 and 41 tie; 41 was better in the held-out season (0.2051 vs
                         # 0.2096) and keeps roster information relevant for a full season, so 41.
                         # Game-level log-loss could not separate any N0 (differences < 1 SE).

INITIAL_ELO = 1500
ELO_MIN = 1380           # observed range of the K=10 engine over 4 seasons is ~1380-1600
ELO_MAX = 1620

# ── Hand-maintained overrides (keep short, date every entry) ──
# NHL player id -> reason. Players listed here are removed from their roster
# before the depth chart is built (long-term injury, suspension, holdout).
# Find ids at https://api-web.nhle.com/v1/roster/<TEAM>/<season>.
PLAYER_OVERRIDES = {
    # 8471214: "2026-09-29 example: out for season (ACL)",
}
GOALIE_OVERRIDES = PLAYER_OVERRIDES  # same table; goalies matter most, listed for discoverability


# ═══════════════════════════════════════════════════════════════════════
# Player data
# ═══════════════════════════════════════════════════════════════════════

def load_player_season(year, mp_dir=None):
    """Read one MoneyPuck season (start year) into {skaters}, {goalies} keyed by NHL player id."""
    import csv
    mp_dir = mp_dir or MONEYPUCK_DIR
    skaters, goalies = {}, {}
    with open(os.path.join(mp_dir, f"skaters_{year}.csv"), newline="") as f:
        for r in csv.DictReader(f):
            if r["situation"] != "all":
                continue
            pid = int(r["playerId"])
            d = skaters.setdefault(pid, {"name": r["name"], "pos": r["position"], "team": r["team"], "gp": 0, "gs": 0.0, "toi": 0.0})
            d["gp"] += int(r["games_played"]); d["gs"] += float(r["gameScore"]); d["toi"] += float(r["icetime"])
    with open(os.path.join(mp_dir, f"goalies_{year}.csv"), newline="") as f:
        for r in csv.DictReader(f):
            if r["situation"] != "all":
                continue
            pid = int(r["playerId"])
            d = goalies.setdefault(pid, {"name": r["name"], "team": r["team"], "gp": 0, "xg": 0.0, "ga": 0.0, "toi": 0.0})
            d["gp"] += int(r["games_played"]); d["xg"] += float(r["xGoals"]); d["ga"] += float(r["goals"]); d["toi"] += float(r["icetime"])
    return skaters, goalies


def load_seasons(years, mp_dir=None):
    """{year: (skaters, goalies)} for every year whose files exist."""
    out = {}
    for y in years:
        try:
            out[y] = load_player_season(y, mp_dir)
        except FileNotFoundError:
            pass
    return out


def pooled_skater(pid, data_by_season, year, n_seasons=SKATER_SEASONS):
    """Pooled GP / Game Score / TOI over seasons year-1 .. year-n_seasons."""
    gp = gs = toi = 0.0
    name = pos = None
    for y in range(year - 1, year - 1 - n_seasons, -1):
        s = data_by_season.get(y, ({}, {}))[0].get(pid)
        if s:
            gp += s["gp"]; gs += s["gs"]; toi += s["toi"]
            name = name or s["name"]; pos = pos or s["pos"]
    return gp, gs, toi, name, pos


def pooled_goalie(pid, data_by_season, year, n_seasons=GOALIE_SEASONS):
    gp = xg = ga = 0.0
    name = None
    for y in range(year - 1, year - 1 - n_seasons, -1):
        g = data_by_season.get(y, ({}, {}))[1].get(pid)
        if g:
            gp += g["gp"]; xg += g["xg"]; ga += g["ga"]
            name = name or g["name"]
    return gp, xg, ga, name


# ═══════════════════════════════════════════════════════════════════════
# Team projection
# ═══════════════════════════════════════════════════════════════════════

def build_team_prior(roster, data_by_season, year, params=None):
    """
    roster: {"skaters": {pid: {"name","pos"}}, "goalies": {pid: {"name"}}}
    Returns a dict with projected points (raw, uncentred) and a breakdown.
    `params` can override module constants (used by the fit script).
    """
    p = dict(
        n_f=N_FORWARDS, n_d=N_DEFENSE, sk_seasons=SKATER_SEASONS, g_seasons=GOALIE_SEASONS,
        min_gp_sk=MIN_GP_SKATER, min_gp_g=MIN_GP_GOALIE, repl=REPLACEMENT_GS_RATE,
        starter_share=STARTER_SHARE, goalie_shrink=GOALIE_SHRINK,
        pts_per_gs=PTS_PER_GS, pts_per_gsax=PTS_PER_GSAX, overrides=PLAYER_OVERRIDES,
    )
    if params:
        p.update(params)

    # ── skaters ──
    fwd, dfn = [], []
    for pid_raw, meta in roster["skaters"].items():
        pid = int(pid_raw)
        if pid in p["overrides"]:
            continue
        gp, gs, toi, name, pos = pooled_skater(pid, data_by_season, year, p["sk_seasons"])
        known = gp >= p["min_gp_sk"]
        entry = {
            "id": pid, "name": meta.get("name") or name, "pos": meta.get("pos") or pos or "?",
            "gp": int(gp), "known": known,
            "rate": (gs / gp) if known else p["repl"],
            "toi_per_gp": (toi / gp) if gp > 0 else 0.0,
        }
        (dfn if entry["pos"] == "D" else fwd).append(entry)
    fwd.sort(key=lambda e: (-e["toi_per_gp"], -e["rate"]))
    dfn.sort(key=lambda e: (-e["toi_per_gp"], -e["rate"]))
    lineup = fwd[:p["n_f"]] + dfn[:p["n_d"]]
    missing = (p["n_f"] - min(len(fwd), p["n_f"])) + (p["n_d"] - min(len(dfn), p["n_d"]))
    skater_gs = 82.0 * (sum(e["rate"] for e in lineup) + missing * p["repl"])

    # ── goalies ──
    goalies = []
    for pid_raw, meta in roster["goalies"].items():
        pid = int(pid_raw)
        if pid in p["overrides"]:
            continue
        gp, xg, ga, name = pooled_goalie(pid, data_by_season, year, p["g_seasons"])
        known = gp >= p["min_gp_g"]
        raw_rate = ((xg - ga) / gp) if known else 0.0
        goalies.append({
            "id": pid, "name": meta.get("name") or name, "gp": int(gp), "known": known,
            "gsax_per_gp_raw": raw_rate, "gsax_per_gp": p["goalie_shrink"] * raw_rate,
        })
    goalies.sort(key=lambda g: (-g["gp"], -g["gsax_per_gp"]))
    starter = goalies[0] if goalies else None
    backup = goalies[1] if len(goalies) > 1 else None
    s = p["starter_share"]
    goalie_gsax = 82.0 * (s * (starter["gsax_per_gp"] if starter else 0.0)
                          + (1 - s) * (backup["gsax_per_gp"] if backup else 0.0))

    proj_points = p["pts_per_gs"] * skater_gs + p["pts_per_gsax"] * goalie_gsax
    return {
        "proj_points_raw": proj_points,
        "skater_gs": skater_gs,
        "goalie_gsax": goalie_gsax,
        "lineup": [{"id": e["id"], "name": e["name"], "pos": e["pos"], "gp": e["gp"], "known": e["known"],
                    "gs_per_gp": round(e["rate"], 3), "toi_per_gp_min": round(e["toi_per_gp"] / 60.0, 1)} for e in lineup],
        "unknown_in_lineup": sum(1 for e in lineup if not e["known"]) + missing,
        "goalies": [{"id": g["id"], "name": g["name"], "gp": g["gp"], "known": g["known"],
                     "gsax_per_gp_raw": round(g["gsax_per_gp_raw"], 3), "gsax_per_gp": round(g["gsax_per_gp"], 3),
                     "role": "starter" if g is starter else ("backup" if g is backup else "third")} for g in goalies],
    }


def priors_to_elo(proj_points_by_team, elo_per_point=None, team_shrink=None):
    """Centre projected points on the league mean, scale to engine Elo, clip."""
    if not proj_points_by_team:
        return {}
    epp = ELO_PER_POINT if elo_per_point is None else elo_per_point
    shrink = TEAM_SHRINK if team_shrink is None else team_shrink
    mean = sum(proj_points_by_team.values()) / len(proj_points_by_team)
    out = {}
    for ab, pts in proj_points_by_team.items():
        e = INITIAL_ELO + epp * shrink * (pts - mean)
        out[ab] = float(min(ELO_MAX, max(ELO_MIN, e)))
    return out


# ═══════════════════════════════════════════════════════════════════════
# Blend
# ═══════════════════════════════════════════════════════════════════════

def blend_weight(games_played, prior_games=None):
    """Weight on the roster prior after `games_played` games: N0 / (N0 + GP)."""
    n0 = ROSTER_PRIOR_GAMES if prior_games is None else prior_games
    if n0 <= 0:
        return 0.0
    return n0 / (n0 + max(0.0, float(games_played)))


def blend_elo_ratings(elo_current, prior_elo, games_played_by_team, prior_games=None):
    """
    Per-team blend of game Elo with the roster prior.
    games_played_by_team: {abbrev: gp}. Teams missing from the prior keep pure Elo.
    Returns (blended, weights).
    """
    blended, weights = {}, {}
    for ab, cur in elo_current.items():
        pri = prior_elo.get(ab)
        if pri is None:
            blended[ab] = cur; weights[ab] = 0.0
            continue
        w = blend_weight(games_played_by_team.get(ab, 0), prior_games)
        blended[ab] = (1 - w) * cur + w * pri
        weights[ab] = w
    return blended, weights


# ═══════════════════════════════════════════════════════════════════════
# File access used by the live pipeline
# ═══════════════════════════════════════════════════════════════════════

def load_prior():
    """Return the parsed data/roster_prior.json or None."""
    try:
        with open(PRIOR_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def prior_age_hours():
    try:
        import time
        return (time.time() - os.path.getmtime(PRIOR_FILE)) / 3600.0
    except OSError:
        return math.inf


def get_prior_elo():
    """{abbrev: prior Elo} from the saved table, or {} if unavailable."""
    data = load_prior()
    if not data or "teams" not in data:
        return {}
    return {ab: float(t["prior_elo"]) for ab, t in data["teams"].items()}


if __name__ == "__main__":
    data = load_prior()
    if not data:
        print("No data/roster_prior.json — run generate_roster_prior.py")
    else:
        print(f"Roster prior for {data.get('season')} (generated {data.get('generated_at')}, fit {data.get('fit_date')})")
        for ab, t in sorted(data["teams"].items(), key=lambda kv: -kv[1]["prior_elo"]):
            print(f"  {ab:4s} proj_pts={t['proj_points']:6.1f}  prior_elo={t['prior_elo']:7.1f}  starter={t['goalies'][0]['name'] if t['goalies'] else '-'}")
