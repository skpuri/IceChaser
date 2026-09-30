# IceChaser — Project Guide for LLMs

## What This Is
NHL playoff probability tracker at icechaser.com. Monte Carlo simulation with Elo-based win probabilities. Updated every 20 minutes via cron during game nights.

---

## Architecture (Single-Pass Design)

**One 100k simulation produces everything.** Do not add separate sim passes, Rust binaries, or redundant computations.

```
generate_data_v3.py (orchestrator)
  → nhl_api.py (fetch standings + schedule + today's games)
  → elo_engine.py (update Elo ratings from completed games)
  → simulator_np.py (100k vectorized Monte Carlo)
      → run_scenario_analysis_vectorized() — odds + scenarios + best/worst + What If
      → _run_odds_and_whatif() — used when no active games tonight
  → narrative.py (generate text summaries)
  → Output: /var/www/icechaser/data/playoff_odds.json
```

## Key Files

| File | Purpose |
|---|---|
| `backend/generate_data_v3.py` | Main orchestrator. Runs on cron. |
| `backend/simulator_np.py` | NumPy-vectorized Monte Carlo engine |
| `backend/elo_engine.py` | Elo rating computation from NHL game results |
| `backend/roster_prior.py` | Roster-aware strength prior (MoneyPuck Game Score + GSAx on current rosters), blended into Elo by games played |
| `backend/generate_roster_prior.py` | Builds `data/roster_prior.json` (32 roster calls, ~10 s). Called by the orchestrator when the table is >24 h old |
| `backend/roster_prior_fit.py` | Fits every roster-prior constant from cached history and runs the backtest (manual, `--sims` takes ~40 min) |
| `backend/patches/` | Orchestrator integration for the roster prior (`roster_prior_integration.patch`) and the schedule-window fix (`schedule_window.patch`), NOT applied |
| `backend/nhl_api.py` | NHL API data fetching |
| `backend/narrative.py` | Text narrative generation |
| `backend/calibration.py` | Historical calibration (run manually) |
| `backend/calibration_tune.py` | Grid search for Elo parameters |
| `/var/www/icechaser/` | Live site (nginx) |
| `/var/www/icechaser/data/playoff_odds.json` | Live data file |
| `data/elo_ratings.json` | Persistent Elo ratings |
| `data/roster_prior.json` | Per-team roster prior (prior Elo, lineup, goalies) |
| `data/moneypuck/` | Cached MoneyPuck season CSVs (completed seasons never change) |
| `data/backtest/` | Cached results, opening rosters, fit log and playoff backtest for `roster_prior_fit.py` |
| `data/calibration_results.json` | Calibration output |
| `METHODOLOGY.md` | Public methodology doc (also served on site) |

## Elo Parameters (CALIBRATED — do not change without re-running calibration)

```python
K_FACTOR = 10        # Slow-moving, NHL teams are stable
HOME_BONUS = 100     # ~64% expected for equal teams at home
OT_DISCOUNT = 0.50   # OT wins barely move ratings
OT_PROBABILITY = 0.24  # Fixed per-game OT rate
```

Calibrated via grid search over 3 seasons (2022-25), 480 predictions, Brier=0.061.

## Roster Prior (FITTED — re-run `roster_prior_fit.py --sims` before changing)

```python
ROSTER_PRIOR_GAMES = 41   # rating = w*prior + (1-w)*Elo, w = 41/(41+GP). The one blend constant.
GOALIE_SHRINK = 0.298     # goalie GSAx regressed hard: year-over-year r is only 0.25
TEAM_SHRINK = 1.172       # scale from projected points to actual (>1: projection compresses spreads)
```

All other constants and their derivations are in the header of `backend/roster_prior.py`.
Injured/suspended players: add their NHL id to `PLAYER_OVERRIDES` there, then run `generate_roster_prior.py`.
The prior is only active once `backend/patches/roster_prior_integration.patch` is applied to `generate_data_v3.py`;
the cron runs that file directly from this directory, so applying the patch IS the deployment.

## Known pre-existing issues (found 2026-09-29, not fixed here)

- `nhl_api.get_remaining_schedule()` only looks 30 days ahead. At season open the simulator receives ~235 of the
  1344 games, so "playoff odds" are really odds after one month. `backend/patches/schedule_window.patch` extends
  the window to `regularSeasonEndDate`; verify the run stays inside the 60 s budget after applying it.
- 2026-27 is an **84-game** season (1344 games). `82` is hardcoded in `nhl_api.parse_standings`, `simulator_np`
  and `calibration.py` (`gamesRemaining`, `pointsPace`). Harmless while the real schedule is used, wrong for pace.
- `elo_engine.SEASON_START` is fixed at 2025-10-07, so ratings carry across seasons un-regressed and the
  replay grows every season. METHODOLOGY says ratings reset each October; the code does not.

## Simulation Constants

- **Sim count:** 100,000 (main pass), 5,000 (forced-outcome best/worst)
- **Win prob clip:** [0.25, 0.75]
- **Elimination threshold:** `playoff_pct <= 0.05` (0.05%, not 0.5%)
- **Scenario delta threshold:** 0.3% (suppress noise for non-own-team games)
- **What If min sample:** 10 sims per record bucket

## Critical Rules

### DO NOT:
- Add a separate base sim — the 100k vectorized pass IS the base sim
- Use the Rust binary (`simulator_rust.py`) — deprecated, all Python now
- Re-simulate OFF/FINAL games — they're already in standings
- Show Western games in Eastern team scenarios (conference filter exists)
- Mark teams with >0.05% as eliminated
- Change Elo parameters without running `calibration_tune.py` first

### MUST:
- Return 4-tuple from `run_scenario_analysis_vectorized()`: `(best_worst, scenarios, odds, what_if)`
- Return 4-tuple `({}, {}, {}, {})` for empty tonight_games (not 2-tuple)
- Handle the no-active-games path — `_run_odds_and_whatif()` runs a full sim even when all games are done
- Track W/L/OTL records in `(n_sims, n_teams, 3)` array — OT winners get a W, not nothing
- Update Elo ratings BEFORE running the sim
- Clear `_elo_ratings_cache = None` when Elo params change

## OT Points (NHL rules, often gets implemented wrong)

```
Home wins regulation: home +2, away +0
Away wins regulation: home +0, away +2
Home wins OT:        home +2, away +1  ← loser gets consolation point
Away wins OT:        home +1, away +2  ← loser gets consolation point
```

OT LOSER ALWAYS GETS 1 POINT. This is the single most common bug in this codebase.

## Record Tracking (What If)

The `all_records` array tracks per-team W/L/OTL:
```python
# CORRECT:
batch_wins[:, h] += hw.astype(np.int8)           # ALL wins (reg + OT)
batch_otl[:, h]  += (~hw & ot).astype(np.int8)   # Loses in OT only
batch_loss[:, h] += (~hw & ~ot).astype(np.int8)  # Loses in regulation only

# WRONG (previous bug):
batch_wins[:, h] += (hw & ~ot).astype(np.int8)   # ← MISSES OT WINS
```

## Cron

- **Job:** `icechaser-odds-updater` (ID: `9fed7b64-b8d9-4e81-b7d8-6dfdf0d77b2b`)
- **Interval:** Every 20 minutes
- **Smart skip:** Exits early if no games today, or if all games FINAL and last update <15 min ago
- **Log:** `/tmp/icechaser_cron.log`

## Performance Budget

Total end-to-end must stay under 60 seconds:
- API fetch: ~2s
- Elo update: ~1s  
- 100k sim + scenarios: ~30-45s
- Tomorrow scenarios: ~15s

If you're adding something that takes longer than 5s, you're probably doing it wrong.

## Calibration (run manually, not on cron)

```bash
# Full calibration against historical seasons
python3 backend/calibration.py

# Grid search for optimal Elo parameters  
python3 backend/calibration_tune.py
```

These use cached game data in `/tmp/nhl_games_*.json`. First run fetches from API (~2 min), subsequent runs use cache.

calibration_tune.py uses 20k sims per checkpoint for speed. Full calibration uses 100k.

## Nginx

- Site: `/etc/nginx/sites-available/icechaser`
- No-cache headers on `/data/` and `.js`/`.css` files
- METHODOLOGY.md served at `/METHODOLOGY.md`

## Common Bugs (historical, fixed)

1. OT loser getting 0 points instead of 1
2. OFF/FINAL games being re-simulated
3. `run_scenario_analysis_vectorized` returning 2-tuple instead of 4 when no games active
4. OT wins not counted as wins in record tracking (What If shows impossible records)
5. Western games showing in Eastern team scenarios
6. What If using `real_schedule` instead of `full_schedule_with_tonight`
