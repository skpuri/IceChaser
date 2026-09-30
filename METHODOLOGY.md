# IceChaser Playoff Odds — Methodology

**Version:** 2.1 (roster prior, not yet deployed)  
**Date:** September 2026  
**Last Calibrated:** April 4, 2026 (3 seasons: 2022-23, 2023-24, 2024-25)

---

## Overview

IceChaser estimates NHL playoff probabilities using **Elo-rated Monte Carlo simulation**. Every remaining regular-season game is simulated 100,000 times using win probabilities derived from each team's Elo rating. The fraction of simulations in which a team qualifies for the playoffs is their playoff probability.

---

## Data Sources

All data comes from the **NHL public API** (`https://api-web.nhle.com/v1/`) in real time:

- **Standings:** Current points, wins, regulation wins, games played, clinch status for all 32 teams
- **Schedule:** Remaining regular-season games (date, home, away)
- **Game results:** Every completed regular-season game this season, used to build Elo ratings
- **Live games:** Current game states (FUT/PRE/LIVE/CRIT/OFF/FINAL) to determine which games are still in play

Games that have already concluded (OFF/FINAL) are **never re-simulated** — their results are reflected in the official standings.

---

## Elo Rating System

Each team carries an **Elo rating** that tracks their strength over the season. Ratings start at 1500 and update after every game.

### Parameters (calibrated)

| Parameter | Value | Meaning |
|---|---|---|
| **K-factor** | 10 | How much a single game moves ratings. Low = stable, high = reactive. |
| **Home bonus** | 100 | Elo points added to the home team before computing win probability. |
| **OT discount** | 0.50 | OT/SO wins move ratings at 50% of a regulation win. |
| **Initial rating** | 1500 | All teams start here at season open. |

These were optimized via grid search over 3 historical seasons (2022-25), minimizing Brier score across 480 predictions at 5 checkpoints per season.

### How ratings update

After each game:

```
expected = 1 / (1 + 10^((opponent_elo - team_elo - home_bonus) / 400))
k = K_FACTOR × (OT_DISCOUNT if overtime else 1.0)
new_elo = old_elo + k × (actual_result - expected)
```

Where `actual_result` is 1 for a win, 0 for a loss.

### Why these values

- **K=10:** NHL teams are very stable week-to-week. A low K prevents one fluky game from distorting a team's rating. This outperformed K=20, 30, and 40 across all test seasons.
- **Home bonus=100:** Translates to roughly 64% expected win rate for equally-rated teams at home. The NHL's historical home win rate is ~54%, but the higher Elo bonus accounts for travel, schedule, and crowd effects that compound beyond raw win rate.
- **OT discount=0.50:** Overtime outcomes are near coin-flips regardless of team quality. Counting them at half weight prevents random OT results from polluting true strength estimates.

---

## Roster-Aware Strength Prior

Elo only knows game results, so at 0 games played it can only carry last season's rating forward. The **roster prior** (`backend/roster_prior.py`) builds an independent strength estimate from the players actually on each roster and blends it with Elo, weighted by how much hockey has been played.

### Source

Per-player season summaries from [MoneyPuck](https://moneypuck.com/data.htm) (free CSV, keyed on NHL player id so roster joins are exact):

- **Skaters:** `gameScore` — Luszczyszyn's Game Score, a published box-score composite. Rate per game pooled over the last **2** seasons (year-over-year r = 0.82).
- **Goalies:** `xGoals − goals` (GSAx) per game pooled over the last **3** seasons, then multiplied by **0.298** — the fitted year-over-year slope. Goalie results barely repeat (r = 0.25), so the model deliberately trusts them little.

Rosters come from the NHL API (`/v1/roster/<TEAM>/<season>`) and are refreshed daily, so trades, signings and call-ups move the prior within a day.

### Team projection

1. Depth chart: top 12 forwards + 6 defensemen on the roster by pooled ice time, 82 games each. Players with < 10 NHL games (rookies, camp extras) are valued at the replacement rate (0.193 GS/game, the 13th–14th F / 7th–8th D).
2. Goaltending: the goalie with the most games in the pooled window is the starter and gets 60.7% of starts (league mean); the backup gets the rest.
3. Points = 52.1 + 0.0542 × team Game Score + 0.277 × team GSAx (R² = 0.79 on 128 realised team-seasons; the GSAx coefficient matches the 0.31 points-per-goal-of-differential fitted separately).
4. Prior Elo = 1500 + 3.234 × 1.172 × (projected points − league mean), clipped to [1380, 1620]. The 3.234 is the engine's own Elo-per-point (R² = 0.97); the 1.172 is the slope of actual on projected points.

### Blend

```
rating = w × prior + (1 − w) × Elo,   w = 41 / (41 + games played)
```

Pure prior on opening night, 50/50 after 41 games, one third after 82. The single constant (41 games) was chosen from the playoff-odds backtest below.

### Backtest (4 seasons, real schedules, 20k sims, Brier per team; lower is better)

| Rating used | GP≈0 | GP≈10 | GP≈20 | GP≈41 | mean |
|---|---|---|---|---|---|
| Flat 1500 | 0.250 | 0.179 | 0.168 | 0.104 | 0.175 |
| Carried Elo (previous behaviour) | 0.222 | 0.174 | 0.154 | 0.109 | 0.165 |
| Carried Elo regressed ⅓ to 1500 | 0.209 | 0.165 | 0.151 | 0.105 | 0.157 |
| Roster prior alone | 0.195 | 0.161 | 0.150 | 0.112 | 0.154 |
| **Blend, N0 = 41** | **0.195** | **0.158** | **0.147** | **0.109** | **0.152** |

Preseason (GP≈0) by season, prior vs carried Elo: 2022-23 0.170 vs 0.204, 2023-24 0.178 vs 0.158, 2024-25 0.205 vs 0.227, 2025-26 (held out) 0.226 vs 0.298. The prior loses one season in four. Full log: `data/backtest/fit_2026-09-29.log`.

Known limitations: rookies and players returning from a lost season are valued at replacement level; the goalie effect is small by construction (an elite starter replaced by an average goalie moves a team ~6 Elo); the preseason spread is compressed relative to reality in two of four seasons and expanded in the other two.

---

## Win Probability Model

For each simulated game:

```
P(home wins) = 1 / (1 + 10^((away_elo - home_elo - 100) / 400))
```

Clipped to [0.25, 0.75] to prevent extreme probabilities.

### Overtime

Each game has a **24% probability** of going to overtime (NHL historical average). If OT occurs:
- Winner gets **2 points**
- Loser gets **1 point** (the "loser point")
- The winner in OT is determined by coin flip (no significant home advantage in OT empirically)

### Full outcome table

| Outcome | Home pts | Away pts |
|---|---|---|
| Home wins regulation | 2 | 0 |
| Away wins regulation | 0 | 2 |
| Home wins OT/SO | 2 | 1 |
| Away wins OT/SO | 1 | 2 |

---

## Playoff Qualification Rules

After simulating all remaining games, playoff qualification follows the **official NHL format**:

1. **Top 3 teams per division** qualify (ranked by points, tiebroken by regulation wins)
2. **Next 2 teams per conference** qualify as wildcards
3. **8 per conference, 16 total**

---

## Simulation Architecture

### Single-pass design

One 100,000-simulation pass produces **everything**:

- **Playoff odds** per team
- **Tonight's game scenarios** (conditional odds given each possible outcome)
- **Best/worst case** tonight (joint forced outcomes across all same-conference games)
- **What If finish table** (playoff odds grouped by W-L-OTL record)
- **Tomorrow's scenarios** (same analysis for upcoming games)

There is no separate base sim, no Rust binary, no redundant computation. One pass, all outputs.

### Record tracking

Each simulation tracks per-team W/L/OTL records for remaining games in a `(100,000 × 32 × 3)` array. This enables the What If table: group simulations by finish record, compute playoff% per group.

### Performance

| Component | Time |
|---|---|
| NHL API fetch (standings + schedule) | ~2s |
| Elo rating update | ~1s |
| 100k vectorized sim + scenarios + What If | ~30-45s |
| Tomorrow's scenarios | ~15s |
| Total end-to-end | **< 60 seconds** |

The simulation uses NumPy vectorized operations — no Python loops over individual simulations.

### Forced-outcome simulations (best/worst case)

For each team's best and worst case tonight: identify optimal/pessimal outcomes for each same-conference game, then run 5,000 sims with those outcomes locked. Captures joint effects (e.g., you need to win AND a rival needs to lose).

---

## Calibration Results

Tested against 3 historical seasons at 5 checkpoints each (30, 20, 15, 10, 5 games remaining).

### Overall

- **Brier Score: 0.061** (0.0 = perfect, 0.25 = random coin flip)
- **480 total predictions** across 96 team-season-checkpoint combinations

### By probability bucket

| Predicted | Actual | Count | Delta |
|---|---|---|---|
| 0-5% | 3.7% | 164 | +3.1% |
| 5-15% | 12.0% | 25 | +2.6% |
| 15-25% | 16.7% | 24 | -3.6% |
| 25-35% | 30.0% | 10 | -0.2% |
| 85-95% | 93.8% | 16 | +2.9% |
| 95-100% | 100% | 178 | +0.4% |

### By games remaining

| Checkpoint | Brier (all) | Brier (bubble 10-90%) |
|---|---|---|
| ~5 games left | 0.035 | 0.145 |
| ~10 games left | 0.037 | 0.139 |
| ~15 games left | 0.053 | 0.171 |
| ~20 games left | 0.078 | 0.210 |
| ~30 games left | 0.113 | 0.230 |

---

## Known Limitations

1. **No game-context factors:** Injuries, back-to-backs, goalie matchups, and motivation are not modeled.
2. **Fixed OT rate:** 24% applied uniformly. Defensive matchups go to OT more often.
3. **Independence assumption:** Each game simulated independently. No fatigue/momentum effects.
4. **Noise at extremes:** Teams below 0.5% should be read as "essentially eliminated," not precisely calibrated.
5. **Season carry-over:** `elo_engine.SEASON_START` is fixed, so Elo carries forward un-regressed across seasons. The roster prior above dominates the blend at season open, which the backtest shows is better than the carried rating alone.

---

## Comparison to Other Models

| Model | Win Prob Basis | Calibrated | Brier |
|---|---|---|---|
| IceChaser v2 | Elo (K=10, HB=100) | ✅ 3 seasons | 0.061 |
| IceChaser v1 | Points pace ratio | ❌ | 0.063 |
| Random baseline | 50/50 | N/A | 0.250 |

---

## Future Improvements

1. **Goal differential integration** — Pythagorean expectation alongside Elo
2. **Season-start Elo** — reset or regress `elo_engine` at each new season (the backtest favours a fresh 1500 start blended with the roster prior: mean Brier 0.150 vs 0.152)
3. **Schedule strength adjustment** — weight remaining opponents
4. **Expanded calibration** — more seasons, finer checkpoints
5. **Team-specific OT rates** — defensive teams go to OT more often

---

*Methodology document auto-generated. For questions, see the calibration data at `/data/calibration_results.json`.*
