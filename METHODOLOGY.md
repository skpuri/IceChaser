# IceChaser Playoff Odds — Methodology

**Version:** 2.0  
**Date:** April 2026  
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
5. **No pre-season carryover:** Elo starts fresh at 1500 each October. Carrying forward (with regression to mean) could improve early-season accuracy.

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
2. **Pre-season Elo carryover** — regress last season's ratings toward 1500
3. **Schedule strength adjustment** — weight remaining opponents
4. **Expanded calibration** — more seasons, finer checkpoints
5. **Team-specific OT rates** — defensive teams go to OT more often

---

*Methodology document auto-generated. For questions, see the calibration data at `/data/calibration_results.json`.*
