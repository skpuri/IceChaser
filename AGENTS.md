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
| `backend/nhl_api.py` | NHL API data fetching |
| `backend/narrative.py` | Text narrative generation |
| `backend/calibration.py` | Historical calibration (run manually) |
| `backend/calibration_tune.py` | Grid search for Elo parameters |
| `/var/www/icechaser/` | Live site (nginx) |
| `/var/www/icechaser/data/playoff_odds.json` | Live data file |
| `data/elo_ratings.json` | Persistent Elo ratings |
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
