use rand::Rng;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::io::{self, Read};

const HOME_WIN_PROB: f64 = 0.54;
const OT_PROBABILITY: f64 = 0.24;

#[derive(Deserialize, Clone)]
struct Team {
    abbrev: String,
    points: f64,
    regulation_wins: f64,
    points_pace: f64,
    conference: u8,  // 0 = Eastern, 1 = Western
    division: u8,    // 0-3
    games_remaining: i32,
}

#[derive(Deserialize)]
struct Game {
    home_idx: usize,
    away_idx: usize,
}

#[derive(Deserialize)]
struct ForcedOutcome {
    home_idx: usize,
    away_idx: usize,
    winner_idx: usize,
    ot_game: bool,
}

#[derive(Deserialize)]
struct SimInput {
    teams: Vec<Team>,
    schedule: Vec<Game>,         // remaining games to simulate
    forced_outcomes: Vec<ForcedOutcome>,  // pre-applied results
    num_simulations: usize,
}

// Brute force input: run all combos of forced games
#[derive(Deserialize)]
struct BruteForceInput {
    teams: Vec<Team>,
    schedule: Vec<Game>,          // remaining schedule (excluding today's/tomorrow's games)
    active_games: Vec<Game>,      // games to brute force (today's or tomorrow's)
    num_sims_per_combo: usize,
    num_outcomes: usize,          // 4 = home_reg, away_reg, home_ot, away_ot
}

#[derive(Serialize)]
struct TeamResult {
    abbrev: String,
    playoff_pct: f64,
    clinched: bool,
    eliminated: bool,
}

#[derive(Serialize)]
struct BruteForceTeamResult {
    abbrev: String,
    playoff_pct: f64,       // baseline from averaging all combos
    best_case: f64,
    worst_case: f64,
    // Per-game scenario averages: game_scenarios[game_idx][outcome] = avg playoff pct
    game_scenarios: Vec<Vec<f64>>,
}

#[derive(Serialize)]
struct BruteForceOutput {
    teams: Vec<BruteForceTeamResult>,
    combo_count: usize,
    sims_per_combo: usize,
}

// Division-conference mapping (hardcoded for NHL)
// div 0,1 = Eastern (Atlantic, Metropolitan), div 2,3 = Western (Central, Pacific)
fn div_to_conf(div: u8) -> u8 {
    if div < 2 { 0 } else { 1 }
}

/// Run a single batch of simulations with given starting points and schedule.
/// Returns playoff counts per team.
fn simulate_batch(
    base_points: &[f64],
    base_reg_wins: &[f64],
    home_probs: &[f64],
    home_idxs: &[usize],
    away_idxs: &[usize],
    teams: &[Team],
    n_sims: usize,
) -> Vec<u64> {
    let n_teams = teams.len();
    let n_games = home_idxs.len();
    let mut counts = vec![0u64; n_teams];
    let mut rng = rand::thread_rng();

    for _ in 0..n_sims {
        let mut pts: Vec<f64> = base_points.to_vec();
        let mut reg: Vec<f64> = base_reg_wins.to_vec();

        // Simulate each game
        for g in 0..n_games {
            let h = home_idxs[g];
            let a = away_idxs[g];
            let home_wins: bool = rng.gen::<f64>() < home_probs[g];
            let goes_to_ot: bool = rng.gen::<f64>() < OT_PROBABILITY;

            if home_wins {
                pts[h] += 2.0;
                if goes_to_ot {
                    pts[a] += 1.0;
                } else {
                    reg[h] += 1.0;
                }
            } else {
                pts[a] += 2.0;
                if goes_to_ot {
                    pts[h] += 1.0;
                } else {
                    reg[a] += 1.0;
                }
            }
        }

        // Determine playoff qualifiers
        determine_playoffs(&pts, &reg, teams, &mut counts);
    }

    counts
}

/// Determine playoff qualifiers for one simulation result.
fn determine_playoffs(pts: &[f64], reg: &[f64], teams: &[Team], counts: &mut [u64]) {
    // For each conference (0=East, 1=West)
    for conf in 0..2u8 {
        let mut wildcard_pool: Vec<(usize, f64, f64)> = Vec::new(); // (idx, pts, reg)

        // For each division in this conference
        let divs: Vec<u8> = if conf == 0 { vec![0, 1] } else { vec![2, 3] };

        for &div in &divs {
            // Collect teams in this division
            let mut div_teams: Vec<(usize, f64, f64)> = Vec::new();
            for (i, t) in teams.iter().enumerate() {
                if t.conference == conf && t.division == div {
                    div_teams.push((i, pts[i], reg[i]));
                }
            }

            // Sort by points desc, reg wins desc
            div_teams.sort_by(|a, b| {
                b.1.partial_cmp(&a.1)
                    .unwrap()
                    .then(b.2.partial_cmp(&a.2).unwrap())
            });

            // Top 3 qualify
            for k in 0..div_teams.len().min(3) {
                counts[div_teams[k].0] += 1;
            }

            // Rest to wildcard pool
            for k in 3..div_teams.len() {
                wildcard_pool.push(div_teams[k]);
            }
        }

        // Sort wildcard pool, top 2 qualify
        wildcard_pool.sort_by(|a, b| {
            b.1.partial_cmp(&a.1)
                .unwrap()
                .then(b.2.partial_cmp(&a.2).unwrap())
        });
        for k in 0..wildcard_pool.len().min(2) {
            counts[wildcard_pool[k].0] += 1;
        }
    }
}

/// Compute home win probabilities for a schedule.
fn compute_home_probs(schedule: &[Game], teams: &[Team]) -> Vec<f64> {
    schedule
        .iter()
        .map(|g| {
            let h_pace = teams[g.home_idx].points_pace;
            let a_pace = teams[g.away_idx].points_pace;
            let total = h_pace + a_pace;
            if total > 0.0 {
                let strength = h_pace / total;
                (strength * HOME_WIN_PROB / 0.5).clamp(0.3, 0.7)
            } else {
                HOME_WIN_PROB
            }
        })
        .collect()
}

/// Apply forced outcomes to base points/reg_wins.
fn apply_forced(
    base_points: &mut Vec<f64>,
    base_reg_wins: &mut Vec<f64>,
    forced: &[ForcedOutcome],
) {
    for f in forced {
        let loser = if f.winner_idx == f.home_idx {
            f.away_idx
        } else {
            f.home_idx
        };
        base_points[f.winner_idx] += 2.0;
        if f.ot_game {
            base_points[loser] += 1.0;
        } else {
            base_reg_wins[f.winner_idx] += 1.0;
        }
    }
}

/// Brute force all 4^N outcome combinations using Rayon for parallelism.
fn brute_force(input: &BruteForceInput) -> BruteForceOutput {
    let n_teams = input.teams.len();
    let n_active = input.active_games.len();
    let n_outcomes = input.num_outcomes; // 4
    let n_combos = n_outcomes.pow(n_active as u32);
    let sims = input.num_sims_per_combo;

    // Pre-compute schedule home probs (excluding active games)
    let home_probs = compute_home_probs(&input.schedule, &input.teams);
    let home_idxs: Vec<usize> = input.schedule.iter().map(|g| g.home_idx).collect();
    let away_idxs: Vec<usize> = input.schedule.iter().map(|g| g.away_idx).collect();

    // Run all combos in parallel
    let combo_results: Vec<(Vec<u64>, Vec<Vec<f64>>)> = (0..n_combos)
        .into_par_iter()
        .map(|combo_idx| {
            // Decode combo into outcomes per game
            let mut base_pts: Vec<f64> = input.teams.iter().map(|t| t.points).collect();
            let mut base_reg: Vec<f64> = input.teams.iter().map(|t| t.regulation_wins).collect();

            // Per-game outcome tracking: which outcome for each game
            let mut outcomes = vec![0usize; n_active];
            let mut temp = combo_idx;
            for g in 0..n_active {
                outcomes[g] = temp % n_outcomes;
                temp /= n_outcomes;
            }

            // Apply forced outcomes
            for (g, &outcome) in outcomes.iter().enumerate() {
                let home = input.active_games[g].home_idx;
                let away = input.active_games[g].away_idx;

                match outcome {
                    0 => {
                        // Home wins regulation
                        base_pts[home] += 2.0;
                        base_reg[home] += 1.0;
                    }
                    1 => {
                        // Away wins regulation
                        base_pts[away] += 2.0;
                        base_reg[away] += 1.0;
                    }
                    2 => {
                        // Home wins OT (away gets consolation)
                        base_pts[home] += 2.0;
                        base_pts[away] += 1.0;
                    }
                    3 => {
                        // Away wins OT (home gets consolation)
                        base_pts[away] += 2.0;
                        base_pts[home] += 1.0;
                    }
                    _ => {}
                }
            }

            // Run sims with these forced results
            let counts = simulate_batch(
                &base_pts,
                &base_reg,
                &home_probs,
                &home_idxs,
                &away_idxs,
                &input.teams,
                sims,
            );

            // Convert counts to percentages for this combo
            let pcts: Vec<f64> = counts
                .iter()
                .map(|&c| (c as f64 / sims as f64) * 100.0)
                .collect();

            // Build per-game outcome sums for averaging later
            // game_outcome_sums[game_idx][outcome] += pct for each team
            let mut game_sums: Vec<Vec<f64>> = vec![vec![0.0; n_teams]; n_active * n_outcomes];
            for g in 0..n_active {
                let o = outcomes[g];
                let row = g * n_outcomes + o;
                for t in 0..n_teams {
                    game_sums[row][t] = pcts[t];
                }
            }

            (counts, game_sums)
        })
        .collect();

    // Aggregate results
    let mut total_counts = vec![0u64; n_teams];

    // Per-game outcome accumulators: [game_idx * n_outcomes + outcome][team] = (sum, count)
    let mut game_sums = vec![vec![(0.0f64, 0u64); n_teams]; n_active * n_outcomes];

    for (counts, gsums) in &combo_results {
        for t in 0..n_teams {
            total_counts[t] += counts[t];
        }

        // Accumulate per-game sums
        for row in 0..(n_active * n_outcomes) {
            for t in 0..n_teams {
                if gsums[row][t] > 0.0 {
                    game_sums[row][t].0 += gsums[row][t];
                    game_sums[row][t].1 += 1;
                }
            }
        }
    }

    let total_sims = n_combos * sims;

    // Build output — compute best/worst from averaged per-game scenarios,
    // not from raw per-combo Monte Carlo results (which have sampling noise)
    let teams_out: Vec<BruteForceTeamResult> = (0..n_teams)
        .map(|t| {
            let baseline = (total_counts[t] as f64 / total_sims as f64) * 100.0;

            // Per-game scenarios: [game_idx] -> [outcome_0_avg, outcome_1_avg, ...]
            let mut scenarios: Vec<Vec<f64>> = Vec::new();
            for g in 0..n_active {
                let mut outcome_avgs = Vec::new();
                for o in 0..n_outcomes {
                    let (sum, count) = game_sums[g * n_outcomes + o][t];
                    if count > 0 {
                        outcome_avgs.push((sum / count as f64 * 100.0).round() / 100.0);
                    } else {
                        outcome_avgs.push(baseline);
                    }
                }
                scenarios.push(outcome_avgs);
            }

            // Compute best/worst by finding the combination of per-game outcomes
            // that maximizes/minimizes the team's odds.
            // For each game, pick the best (or worst) single-game outcome average,
            // then estimate the combined effect additively from baseline.
            // This is accurate because the per-game deltas are near-independent.
            let mut best_total_delta = 0.0f64;
            let mut worst_total_delta = 0.0f64;
            for g in 0..n_active {
                let game_avgs = &scenarios[g];
                if game_avgs.is_empty() { continue; }
                let game_best = game_avgs.iter().cloned().fold(f64::MIN, f64::max);
                let game_worst = game_avgs.iter().cloned().fold(f64::MAX, f64::min);
                best_total_delta += game_best - baseline;
                worst_total_delta += game_worst - baseline;
            }

            let best_case = (baseline + best_total_delta).clamp(0.0, 100.0);
            let worst_case = (baseline + worst_total_delta).clamp(0.0, 100.0);

            BruteForceTeamResult {
                abbrev: input.teams[t].abbrev.clone(),
                playoff_pct: (baseline * 10.0).round() / 10.0,
                best_case: (best_case * 10.0).round() / 10.0,
                worst_case: (worst_case * 10.0).round() / 10.0,
                game_scenarios: scenarios,
            }
        })
        .collect();

    BruteForceOutput {
        teams: teams_out,
        combo_count: n_combos,
        sims_per_combo: sims,
    }
}

// ─── What If Mode ─────────────────────────────────────────────────────────────
// Run full simulations and track the target team's final record in each sim.
// Group by (wins, otl) → count occurrences + playoff makes per bucket.
// This gives both frequency ("how likely is this record") and playoff % per record.

#[derive(Deserialize)]
struct WhatIfInput {
    teams: Vec<Team>,
    schedule: Vec<Game>,        // FULL remaining schedule (including target team's games)
    target_idx: usize,
    target_games_left: usize,   // unused but kept for compat
    num_simulations: usize,
}

#[derive(Serialize)]
struct WhatIfRecord {
    wins: usize,
    ot_losses: usize,
    reg_losses: usize,
    final_points: f64,
    times: u64,                 // how many sims ended with this record
    made_playoffs: u64,         // how many of those made playoffs
    playoff_pct: f64,
}

#[derive(Serialize)]
struct WhatIfOutput {
    abbrev: String,
    current_points: f64,
    current_reg_wins: f64,
    games_left: usize,
    total_sims: usize,
    records: Vec<WhatIfRecord>,
}

fn what_if(input: &WhatIfInput) -> WhatIfOutput {
    let n_teams = input.teams.len();
    let tidx = input.target_idx;
    let sims = input.num_simulations;

    let base_pts: Vec<f64> = input.teams.iter().map(|t| t.points).collect();
    let base_reg: Vec<f64> = input.teams.iter().map(|t| t.regulation_wins).collect();

    let home_probs = compute_home_probs(&input.schedule, &input.teams);
    let home_idxs: Vec<usize> = input.schedule.iter().map(|g| g.home_idx).collect();
    let away_idxs: Vec<usize> = input.schedule.iter().map(|g| g.away_idx).collect();
    let n_games = home_idxs.len();

    // Identify which games involve the target team
    let mut target_game_indices: Vec<usize> = Vec::new();
    let mut target_is_home: Vec<bool> = Vec::new();
    for g in 0..n_games {
        if home_idxs[g] == tidx {
            target_game_indices.push(g);
            target_is_home.push(true);
        } else if away_idxs[g] == tidx {
            target_game_indices.push(g);
            target_is_home.push(false);
        }
    }

    // Run sims in parallel chunks
    let chunk_size = 1000;
    let n_chunks = (sims + chunk_size - 1) / chunk_size;

    // Each chunk returns: Vec<(wins, otl, reg_losses, final_pts, made_playoffs)>
    let chunk_results: Vec<Vec<(usize, usize, usize, f64, bool)>> = (0..n_chunks)
        .into_par_iter()
        .map(|chunk_idx| {
            let chunk_sims = if chunk_idx == n_chunks - 1 {
                sims - chunk_idx * chunk_size
            } else {
                chunk_size
            };

            let mut rng = rand::thread_rng();
            let mut results = Vec::with_capacity(chunk_sims);

            for _ in 0..chunk_sims {
                let mut pts: Vec<f64> = base_pts.clone();
                let mut reg: Vec<f64> = base_reg.clone();

                // Track target team's record
                let mut t_wins: usize = 0;
                let mut t_otl: usize = 0;
                let mut t_reg_losses: usize = 0;

                for g in 0..n_games {
                    let h = home_idxs[g];
                    let a = away_idxs[g];
                    let home_wins: bool = rng.gen::<f64>() < home_probs[g];
                    let goes_to_ot: bool = rng.gen::<f64>() < OT_PROBABILITY;

                    if home_wins {
                        pts[h] += 2.0;
                        if goes_to_ot {
                            pts[a] += 1.0;
                        } else {
                            reg[h] += 1.0;
                        }
                        // Track target record
                        if h == tidx { t_wins += 1; }
                        else if a == tidx {
                            if goes_to_ot { t_otl += 1; } else { t_reg_losses += 1; }
                        }
                    } else {
                        pts[a] += 2.0;
                        if goes_to_ot {
                            pts[h] += 1.0;
                        } else {
                            reg[a] += 1.0;
                        }
                        // Track target record
                        if a == tidx { t_wins += 1; }
                        else if h == tidx {
                            if goes_to_ot { t_otl += 1; } else { t_reg_losses += 1; }
                        }
                    }
                }

                // Check if target made playoffs
                let mut made_playoffs = false;
                let mut counts = vec![0u64; n_teams];
                determine_playoffs(&pts, &reg, &input.teams, &mut counts);
                if counts[tidx] > 0 {
                    made_playoffs = true;
                }

                results.push((t_wins, t_otl, t_reg_losses, pts[tidx], made_playoffs));
            }

            results
        })
        .collect();

    // Aggregate into buckets by (wins, otl)
    use std::collections::HashMap;
    let mut buckets: HashMap<(usize, usize), (u64, u64, f64)> = HashMap::new(); // (times, made, pts_sum)

    for chunk in &chunk_results {
        for &(wins, otl, _reg_l, final_pts, made) in chunk {
            let entry = buckets.entry((wins, otl)).or_insert((0, 0, 0.0));
            entry.0 += 1;
            if made { entry.1 += 1; }
            entry.2 += final_pts;
        }
    }

    // Convert to sorted records
    let gl = target_game_indices.len();
    let mut records: Vec<WhatIfRecord> = buckets
        .into_iter()
        .map(|((wins, otl), (times, made, pts_sum))| {
            let reg_losses = if gl >= wins + otl { gl - wins - otl } else { 0 };
            WhatIfRecord {
                wins,
                ot_losses: otl,
                reg_losses,
                final_points: (pts_sum / times as f64 * 10.0).round() / 10.0,
                times,
                made_playoffs: made,
                playoff_pct: if times > 0 {
                    ((made as f64 / times as f64) * 1000.0).round() / 10.0
                } else {
                    0.0
                },
            }
        })
        .collect();

    // Sort by wins desc, then otl desc
    records.sort_by(|a, b| b.wins.cmp(&a.wins).then(b.ot_losses.cmp(&a.ot_losses)));

    WhatIfOutput {
        abbrev: input.teams[tidx].abbrev.clone(),
        current_points: base_pts[tidx],
        current_reg_wins: base_reg[tidx],
        games_left: gl,
        total_sims: sims,
        records,
    }
}

fn main() {
    // Read JSON from stdin
    let mut input_str = String::new();
    io::stdin().read_to_string(&mut input_str).unwrap();

    // Detect mode from input
    if input_str.contains("\"active_games\"") {
        // Brute force mode
        let input: BruteForceInput = serde_json::from_str(&input_str).unwrap();
        let output = brute_force(&input);
        println!("{}", serde_json::to_string(&output).unwrap());
    } else if input_str.contains("\"target_idx\"") {
        // What If mode
        let input: WhatIfInput = serde_json::from_str(&input_str).unwrap();
        let output = what_if(&input);
        println!("{}", serde_json::to_string(&output).unwrap());
    } else {
        // Simple simulation mode
        let input: SimInput = serde_json::from_str(&input_str).unwrap();
        let n_teams = input.teams.len();

        let mut base_pts: Vec<f64> = input.teams.iter().map(|t| t.points).collect();
        let mut base_reg: Vec<f64> = input.teams.iter().map(|t| t.regulation_wins).collect();

        apply_forced(&mut base_pts, &mut base_reg, &input.forced_outcomes);

        let home_probs = compute_home_probs(&input.schedule, &input.teams);
        let home_idxs: Vec<usize> = input.schedule.iter().map(|g| g.home_idx).collect();
        let away_idxs: Vec<usize> = input.schedule.iter().map(|g| g.away_idx).collect();

        let counts = simulate_batch(
            &base_pts,
            &base_reg,
            &home_probs,
            &home_idxs,
            &away_idxs,
            &input.teams,
            input.num_simulations,
        );

        let results: Vec<TeamResult> = (0..n_teams)
            .map(|i| {
                let pct = (counts[i] as f64 / input.num_simulations as f64) * 100.0;
                TeamResult {
                    abbrev: input.teams[i].abbrev.clone(),
                    playoff_pct: (pct * 10.0).round() / 10.0,
                    clinched: pct >= 99.5,
                    eliminated: pct <= 0.5,
                }
            })
            .collect();

        println!("{}", serde_json::to_string(&results).unwrap());
    }
}
