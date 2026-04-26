import simulator_np as sim
import nhl_api
import numpy as np

teams, today_games = nhl_api.get_all_data()
real_schedule = nhl_api.get_remaining_schedule()
sim.run_simulations_np._real_schedule = real_schedule

# Find WSH vs NYR game
wsh_nyr = next(g for g in today_games
               if set([g.get('homeTeamAbbrev'), g.get('awayTeamAbbrev')]) == {'WSH', 'NYR'})
print(f'WSH vs NYR game: home={wsh_nyr["homeTeamAbbrev"]} away={wsh_nyr["awayTeamAbbrev"]}')

all_abbrevs = [t['teamAbbrev'] for t in teams]
abbrev_to_idx = {a: i for i, a in enumerate(all_abbrevs)}
print(f'abbrev_to_idx[WSH] = {abbrev_to_idx["WSH"]}')
print(f'abbrev_to_idx[NYR] = {abbrev_to_idx["NYR"]}')
print()

valid_games = [g for g in today_games
               if g.get('gameState', '') not in ('FINAL', 'OFF')
               and g.get('homeTeamAbbrev') in abbrev_to_idx
               and g.get('awayTeamAbbrev') in abbrev_to_idx]

gi = None
for idx, g in enumerate(valid_games):
    s1 = set([g.get('homeTeamAbbrev'), g.get('awayTeamAbbrev')])
    s2 = {'WSH', 'NYR'}
    if s1 == s2:
        gi = idx
        break

print(f'Game index in valid_games: {gi}')
print(f'At that index: home={valid_games[gi]["homeTeamAbbrev"]} away={valid_games[gi]["awayTeamAbbrev"]}')
print()

# Call run_scenario_analysis_vectorized to get the raw arrays
# It returns (best_worst, team_scenarios, vect_odds, what_if_data, seed_probs)
result = sim.run_scenario_analysis_vectorized(
    teams, today_games, num_simulations=10000, real_schedule=real_schedule
)
best_worst, team_scenarios, vect_odds, _, _, _ = result
print(f'WSH baseline (vect_odds): {vect_odds.get("WSH")}')
print(f'WSH best_worst: {best_worst.get("WSH")}')
print()

# Now inspect the raw arrays directly
# Re-run the simulation portion to get the arrays
# We need to replicate what run_scenario_analysis_vectorized does to get the raw data

# From run_scenario_analysis_vectorized code:
n_teams = len(teams)
abbrev_to_idx_local = {t["teamAbbrev"]: i for i, t in enumerate(teams)}
idx_to_abbrev_local = {i: t["teamAbbrev"] for i, t in enumerate(teams)}

base_points   = np.array([t["points"] for t in teams], dtype=np.float64)
base_reg_wins = np.array([t.get("regulationWins", 0) for t in teams], dtype=np.float64)
pts_pace      = np.array([t.get("pointsPace", 0) for t in teams], dtype=np.float64)

elo_ratings = sim._load_elo_ratings()
if elo_ratings:
    elo_array = np.array([elo_ratings.get(t["teamAbbrev"], 1500) for t in teams], dtype=np.float64)
else:
    elo_array = None

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
    div_teams_list = [np.where((team_conf == conf_idx) & (team_div == d))[0] for d in conf_divs]
    conf_div_structure.append(div_teams_list)

tonight_pairs_set = {(g["homeTeamAbbrev"], g["awayTeamAbbrev"]) for g in valid_games}
if real_schedule:
    rs_filtered = [(h, a) for h, a in real_schedule
                   if h in abbrev_to_idx_local and a in abbrev_to_idx_local
                   and (h, a) not in tonight_pairs_set]
    combined_home = [abbrev_to_idx_local[g["homeTeamAbbrev"]] for g in valid_games] + \
                    [abbrev_to_idx_local[h] for h, a in rs_filtered]
    combined_away = [abbrev_to_idx_local[g["awayTeamAbbrev"]] for g in valid_games] + \
                    [abbrev_to_idx_local[a] for h, a in rs_filtered]
    sched_home = np.array(combined_home, dtype=np.int32)
    sched_away = np.array(combined_away, dtype=np.int32)
    use_real = True
else:
    use_real = False

num_simulations = 10000
rng = np.random.default_rng()
n_tonight = len(valid_games)

all_outcomes   = np.empty((num_simulations, n_tonight), dtype=np.int8)
all_playoffs   = np.zeros((num_simulations, n_teams),   dtype=np.bool_)
all_records    = np.zeros((num_simulations, n_teams, 3), dtype=np.int8)
all_final_pts  = np.zeros((num_simulations, n_teams),   dtype=np.float32)
all_final_reg  = np.zeros((num_simulations, n_teams),   dtype=np.float32)

batch_size = 5000
sim_offset = 0
while sim_offset < num_simulations:
    b = min(batch_size, num_simulations - sim_offset)

    batch_wins = np.zeros((b, n_teams), dtype=np.int16)
    batch_otl  = np.zeros((b, n_teams), dtype=np.int16)
    batch_loss = np.zeros((b, n_teams), dtype=np.int16)

    batch_pts_home = np.full((b, n_teams), base_points, dtype=np.float64)
    batch_pts_away = np.full((b, n_teams), base_points, dtype=np.float64)
    batch_reg_home = np.full((b, n_teams), base_reg_wins, dtype=np.float64)
    batch_reg_away = np.full((b, n_teams), base_reg_wins, dtype=np.float64)

    remaining = (b, n_teams)
    home_strength = 0.5

    if use_real:
        schedule_home = np.take(sched_home, np.arange(sim_offset, sim_offset + b), mode='wrap')
        schedule_away = np.take(sched_away, np.arange(sim_offset, sim_offset + b), mode='wrap')
        tonight_mask_home = np.zeros((b, n_teams), dtype=np.bool_)
        tonight_mask_away = np.zeros((b, n_teams), dtype=np.bool_)
        for ti in range(n_tonight):
            tonight_mask_home[np.arange(b), schedule_home[:, ti]] = True
            tonight_mask_away[np.arange(b), schedule_away[:, ti]] = True

        for ti in range(n_tonight):
            home_idxs = schedule_home[:, ti]
            away_idxs = schedule_away[:, ti]
            if elo_array is not None:
                hw_prob = sim._elo_win_probs(home_idxs, away_idxs, elo_array)
            else:
                hw_prob = np.clip(np.where(np.full(b, home_strength) > 0,
                                         np.full(b, home_strength) * home_strength / 0.5, 0.5)
                                 * home_strength / 0.5, 0.3, 0.7)
            hw_prob = np.clip(hw_prob, 0.3, 0.7)
            ot = rng.random(b) < 0.24
            outcome = np.where(rng.random(b) < hw_prob, 0, 1)
            outcome = np.where(ot, 2 + rng.integers(0, 2, b), outcome)
            all_outcomes[sim_offset:sim_offset + b, ti] = outcome
            batch_pts_home[np.arange(b), home_idxs] += np.where(outcome == 0, 2, 1)
            batch_pts_away[np.arange(b), away_idxs] += np.where(outcome == 1, 2, 1)
            batch_pts_away[np.arange(b), home_idxs] += np.where(outcome == 0, 0, 1)
            batch_pts_home[np.arange(b), away_idxs] += np.where(outcome == 1, 0, 1)
            batch_reg_home[np.arange(b), home_idxs] += np.where(outcome == 0, 1, 0)
            batch_reg_away[np.arange(b), away_idxs] += np.where(outcome == 1, 1, 0)

        future_home = sched_home[n_tonight:]
        future_away = sched_away[n_tonight:]
        n_future = len(future_home)

        for si in range(b):
            home_idxs = future_home
            away_idxs = future_away
            if elo_array is not None:
                hw_prob = sim._elo_win_probs(home_idxs, away_idxs, elo_array)
            else:
                hw_prob = np.full(len(home_idxs), home_strength)
            hw_prob = np.clip(hw_prob, 0.3, 0.7)
            ot = rng.random(len(home_idxs)) < 0.24
            outcome = np.where(rng.random(len(home_idxs)) < hw_prob, 0, 1)
            outcome = np.where(ot, 2 + rng.integers(0, 2, len(home_idxs)), outcome)
            batch_pts_home[si, future_home] += np.where(outcome == 0, 2, 1)
            batch_pts_away[si, future_away] += np.where(outcome == 1, 2, 1)
            batch_pts_away[si, future_home] += np.where(outcome == 0, 0, 1)
            batch_pts_home[si, future_away] += np.where(outcome == 1, 0, 1)
            batch_reg_home[si, future_home] += np.where(outcome == 0, 1, 0)
            batch_reg_away[si, future_away] += np.where(outcome == 1, 1, 0)

    batch_wins = batch_pts_home - batch_pts_away
    batch_otl  = batch_pts_away - batch_pts_home - batch_reg_away
    batch_loss = batch_pts_home - batch_pts_away - batch_reg_home

    all_records[sim_offset:sim_offset + b, :, 0] = batch_wins
    all_records[sim_offset:sim_offset + b, :, 1] = batch_otl
    all_records[sim_offset:sim_offset + b, :, 2] = batch_loss
    all_final_pts[sim_offset:sim_offset + b, :] = batch_pts_home + batch_pts_away
    all_final_reg[sim_offset:sim_offset + b, :] = batch_reg_home

    sim_offset += b

# NOW determine playoffs using vectorized logic
# We need to run this through the playoff determination
# Let me just look at the outcomes directly since we have them in all_outcomes

wsh_idx = abbrev_to_idx_local['WSH']
nyr_idx = abbrev_to_idx_local['NYR']

print(f'WSH idx={wsh_idx}, NYR idx={nyr_idx}')
print(f'Total sims: {num_simulations}')
print()

# Check raw outcome distribution
game_outcomes = all_outcomes[:, gi]
print(f'Outcome distribution for game {gi}:')
for code, label in [(0,'home_reg'),(1,'away_reg'),(2,'home_OT'),(3,'away_OT')]:
    mask = (game_outcomes == code)
    n = mask.sum()
    print(f'  code {code} ({label}): n={n} ({n/num_simulations*100:.1f}%)')

print()
print(f'Since home=NYR, away=WSH:')
print(f'  code 0 = NYR reg win, code 1 = WSH reg win')
print(f'  code 2 = NYR OT win,  code 3 = WSH OT win')
print()

# The real question: all_playoffs[sim, wsh_idx] should be True/False for WSH making playoffs
# But all_playoffs is still all False because we never computed it!
# Let me check: we have all_records but not the playoff determination
# The run_scenario_analysis_vectorized SHOULD have computed all_playoffs...
# Let me re-run through the actual function but grab the arrays

# Call the function but patch to capture arrays
# Actually let me just look at team_scenarios output which was already computed
print('From team_scenarios (already computed by run_scenario_analysis_vectorized):')
wsh_sc = team_scenarios.get('WSH', [])
nyr_vs_wsh = next((s for s in wsh_sc if s['home_team'] == 'NYR' and s['away_team'] == 'WSH'), None)
if nyr_vs_wsh:
    print(f'  home_reg_win: {nyr_vs_wsh["if_home_reg_win_pct"]}%')
    print(f'  away_reg_win: {nyr_vs_wsh["if_away_reg_win_pct"]}%')
    print(f'  home_OT_win:  {nyr_vs_wsh["if_home_ot_win_pct"]}%')
    print(f'  away_OT_win:  {nyr_vs_wsh["if_away_ot_win_pct"]}%')
    print(f'  baseline_pct: {vect_odds.get("WSH")}%')

# The key issue: in run_scenario_analysis_vectorized, the team_scenarios loop
# computes playoff_col = all_playoffs[:, team_idx]
# But all_playoffs might not be correctly computed yet at that point!
# Let me check: all_playoffs gets filled in during the loop, but when?
# Looking at the code, it seems all_playoffs IS filled via _determine_playoffs_vectorized
# Let me look at where that happens

# Actually let me look at what the function ACTUALLY has for all_playoffs
# by running the playoff determination
# I need to figure out what all_playoffs looks like at line 834
# Let me trace the code...

# Looking at lines 730-834:
# all_playoffs is created at line 722 as zeros
# It's filled during the loop in lines 771-825
# But the team_scenarios loop (lines 835-936) runs AFTER the sim loop
# So all_playoffs should be fully computed by then

# The key question is: does _determine_playoffs_vectorized use all_records correctly?
# Let me check what all_records looks like for WSH
print()
print(f'Sample all_records for WSH (first 20 sims):')
for si in range(min(20, num_simulations)):
    w, o, l = all_records[si, wsh_idx, :]
    pts = base_points[wsh_idx] + (w - l) * 2 + o
    print(f'  sim {si}: W={w} OTL={o} L={l} pts={pts:.0f}  playoff={all_playoffs[si, wsh_idx]}')
