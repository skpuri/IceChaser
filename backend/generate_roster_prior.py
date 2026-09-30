"""
Build data/roster_prior.json — the per-team roster-aware strength prior.

Mirrors BaseChaser's generate_player_war.py: fetch the public player-value
source, join it to current rosters, write one table the simulation reads.

    python3 backend/generate_roster_prior.py            # fetch rosters, (re)use cached MoneyPuck files
    python3 backend/generate_roster_prior.py --offline  # no network: rebuild from cached files only
    python3 backend/generate_roster_prior.py --refresh-moneypuck   # re-download the player files

Sources (both free, no auth):
  * MoneyPuck season summaries, one CSV per season, keyed on NHL player id:
      https://moneypuck.com/moneypuck/playerData/seasonSummary/<year>/regular/skaters.csv
      https://moneypuck.com/moneypuck/playerData/seasonSummary/<year>/regular/goalies.csv
    Cached in data/moneypuck/. Completed seasons never change, so these are
    only re-downloaded on --refresh-moneypuck (do that once each summer when
    the new season's file appears, and bump nothing else — the season is
    derived from the NHL API).
  * NHL API rosters: https://api-web.nhle.com/v1/roster/<TEAM>/<season>
    Fetched every run (32 calls, ~5 s). Cached in data/rosters_<season>.json
    so --offline and failures can fall back to the last good copy.

Runs in ~10 s. generate_data_v3.py calls main() automatically when the table is
older than roster_prior.PRIOR_MAX_AGE_HOURS; run it by hand after a trade,
signing or injury you want reflected immediately.
"""

import os
import sys
import json
import time
import argparse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import roster_prior as rp

MP_URL = "https://moneypuck.com/moneypuck/playerData/seasonSummary/{year}/regular/{kind}.csv"
NHL = "https://api-web.nhle.com/v1"
UA = {"User-Agent": "IceChaser/1.0 (roster prior)"}


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def current_season_id():
    """e.g. 20262027, from the NHL schedule endpoint (falls back to the calendar)."""
    try:
        raw = json.loads(_get(f"{NHL}/schedule/now", timeout=15))
        seasons = {g.get("season") for d in raw.get("gameWeek", []) for g in d.get("games", []) if g.get("season")}
        if seasons:
            return int(max(seasons))
    except Exception:
        pass
    now = datetime.now()
    y = now.year if now.month >= 7 else now.year - 1
    return int(f"{y}{y + 1}")


def team_abbrevs():
    raw = json.loads(_get(f"{NHL}/standings/now", timeout=15))
    return sorted(e["teamAbbrev"]["default"] for e in raw.get("standings", []))


def fetch_moneypuck(years, refresh=False):
    os.makedirs(rp.MONEYPUCK_DIR, exist_ok=True)
    fetched = []
    for y in years:
        for kind in ("skaters", "goalies"):
            path = os.path.join(rp.MONEYPUCK_DIR, f"{kind}_{y}.csv")
            if os.path.exists(path) and not refresh:
                continue
            data = _get(MP_URL.format(year=y, kind=kind))
            if not data.startswith(b"playerId"):
                raise RuntimeError(f"unexpected MoneyPuck payload for {kind} {y}")
            with open(path, "wb") as f:
                f.write(data)
            fetched.append(os.path.basename(path))
            time.sleep(1.5)
    return fetched


def fetch_rosters(season_id, abbrevs):
    rosters = {}
    for ab in abbrevs:
        r = json.loads(_get(f"{NHL}/roster/{ab}/{season_id}", timeout=15))
        rosters[ab] = {
            "skaters": {str(p["id"]): {"name": f'{p["firstName"]["default"]} {p["lastName"]["default"]}', "pos": p["positionCode"]}
                        for k in ("forwards", "defensemen") for p in r.get(k, [])},
            "goalies": {str(p["id"]): {"name": f'{p["firstName"]["default"]} {p["lastName"]["default"]}'}
                        for p in r.get("goalies", [])},
        }
        time.sleep(0.05)
    return rosters


def build(season_id, rosters, data_by_season, source_note):
    year = int(str(season_id)[:4])
    teams = {}
    for ab, roster in rosters.items():
        t = rp.build_team_prior(roster, data_by_season, year)
        teams[ab] = t
    prior_elo = rp.priors_to_elo({ab: t["proj_points_raw"] for ab, t in teams.items()})
    mean = sum(t["proj_points_raw"] for t in teams.values()) / len(teams)
    out_teams = {}
    for ab, t in teams.items():
        out_teams[ab] = {
            "prior_elo": round(prior_elo[ab], 1),
            "proj_points": round(rp.PTS_INTERCEPT + t["proj_points_raw"], 1),
            "proj_points_vs_mean": round(t["proj_points_raw"] - mean, 1),
            "skater_gs": round(t["skater_gs"], 1),
            "goalie_gsax": round(t["goalie_gsax"], 2),
            "unknown_in_lineup": t["unknown_in_lineup"],
            "lineup": t["lineup"],
            "goalies": t["goalies"],
        }
    return {
        "season": season_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fit_date": rp.FIT_DATE,
        "source": source_note,
        "constants": {k: getattr(rp, k) for k in ("SKATER_SEASONS", "GOALIE_SEASONS", "PTS_PER_GS", "PTS_PER_GSAX",
                                                    "REPLACEMENT_GS_RATE", "STARTER_SHARE", "GOALIE_SHRINK", "TEAM_SHRINK",
                                                    "ELO_PER_POINT", "ROSTER_PRIOR_GAMES", "ELO_MIN", "ELO_MAX")},
        "overrides_applied": {str(k): v for k, v in rp.PLAYER_OVERRIDES.items()},
        "teams": out_teams,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="use cached MoneyPuck files and cached rosters only")
    ap.add_argument("--refresh-moneypuck", action="store_true", help="re-download the MoneyPuck season files")
    ap.add_argument("--season", type=int, help="season id, e.g. 20262027 (default: from NHL API)")
    args = ap.parse_args(argv)

    season_id = args.season or (None if args.offline else current_season_id())
    rosters_cache = None
    if season_id is None:
        # offline without --season: reuse whatever the last table was built for
        prev = rp.load_prior()
        if not prev:
            print("offline and no previous table: pass --season"); return 1
        season_id = int(prev["season"])
    year = int(str(season_id)[:4])
    years = list(range(year - max(rp.SKATER_SEASONS, rp.GOALIE_SEASONS), year))
    rosters_cache = os.path.join(rp.DATA_DIR, f"rosters_{season_id}.json")

    print(f"Roster prior for {season_id} (player data seasons {years[0]}-{years[0]+1} .. {years[-1]}-{years[-1]+1})")
    if not args.offline:
        fetched = fetch_moneypuck(years, refresh=args.refresh_moneypuck)
        print(f"   MoneyPuck: {'fetched ' + ', '.join(fetched) if fetched else 'cached files reused'}")
    data_by_season = rp.load_seasons(years, rp.MONEYPUCK_DIR)
    missing = [y for y in years if y not in data_by_season]
    if missing:
        print(f"   ✗ missing MoneyPuck files for {missing} (run without --offline)"); return 1

    if args.offline:
        rosters = json.load(open(rosters_cache))
        roster_note = f"cached rosters {rosters_cache}"
    else:
        try:
            rosters = fetch_rosters(season_id, team_abbrevs())
            json.dump(rosters, open(rosters_cache, "w"))
            roster_note = f"NHL API rosters fetched {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
        except Exception as e:
            if not os.path.exists(rosters_cache):
                raise
            rosters = json.load(open(rosters_cache))
            roster_note = f"roster fetch failed ({e}); cached rosters reused"
            print(f"   ⚠ {roster_note}")
    if len(rosters) != 32:
        print(f"   ✗ expected 32 rosters, got {len(rosters)} — not writing"); return 1

    table = build(season_id, rosters, data_by_season, {
        "players": f"MoneyPuck season summaries {years[0]}-{years[-1]} ({MP_URL})",
        "rosters": roster_note,
        "method": "see backend/roster_prior.py docstring",
    })
    os.makedirs(os.path.dirname(rp.PRIOR_FILE), exist_ok=True)
    tmp = rp.PRIOR_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(table, f, indent=1)
    os.replace(tmp, rp.PRIOR_FILE)

    teams = table["teams"]
    unk = sum(t["unknown_in_lineup"] for t in teams.values())
    print(f"   ✓ wrote {rp.PRIOR_FILE} ({len(teams)} teams, {unk} replacement-valued lineup slots league-wide)")
    for ab, t in sorted(teams.items(), key=lambda kv: -kv[1]["prior_elo"]):
        g = t["goalies"][0]["name"] if t["goalies"] else "-"
        print(f"     {ab:4s} prior_elo={t['prior_elo']:6.1f}  proj={t['proj_points']:5.1f} pts ({t['proj_points_vs_mean']:+5.1f})  unknown={t['unknown_in_lineup']}  starter={g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
