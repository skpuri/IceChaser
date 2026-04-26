import os
os.chdir('/root/.openclaw/workspace/projects/icechaser/backend')

with open('simulator_np.py', 'rb') as f:
    data = f.read()

lines = data.split(b'\n')
line_list = list(lines)

# Find exact boundaries
is_rival_start = None
sched_end = None
best_forced_line = None
end_marker_line = None

for i, line in enumerate(line_list):
    if b'    def _is_rival_in_game' in line:
        is_rival_start = i
    if b'    sched_for_forced = real_schedule' in line:
        sched_end = i
    if b'        best_forced = []' in line:
        best_forced_line = i
    if b'        "has_game": abbrev in teams_playing,' in line and best_forced_line and i > best_forced_line:
        end_marker_line = i

print(f"Removing helpers lines {is_rival_start+1} to {sched_end+1}")
print(f"Replacing game block lines {best_forced_line+1} to {end_marker_line+1}")

new_lines = (
    b'        # Outcome codes in all_outcomes: 0=home reg, 1=away reg, 2=home OT win, 3=away OT win',
    b'        # Pre-compute per-game favorable/unfavorable outcome code sets',
    b'        game_favorable = []   # outcome codes that help THIS team',
    b'        game_unfavorable = [] # outcome codes that hurt THIS team',
    b'        for game in valid_games:',
    b'            home = game["homeTeamAbbrev"]',
    b'            away = game["awayTeamAbbrev"]',
    b'            home_conf = next((t["conference"] for t in teams if t["teamAbbrev"] == home), None)',
    b'            away_conf = next((t["conference"] for t in teams if t["teamAbbrev"] == away), None)',
    b'            is_own = abbrev in (home, away)',
    b'            if not is_own and team_conf_name not in {home_conf, away_conf}:',
    b'                game_favorable.append(None)',
    b'                game_unfavorable.append(None)',
    b'                continue',
    b'',
    b'            rival = None',
    b'            if home in rivals and away in rivals:',
    b'                rival = "both"',
    b'            elif home in rivals:',
    b'                rival = home',
    b'            elif away in rivals:',
    b'                rival = away',
    b'',
    b'            if is_own:',
    b'                if home == abbrev:',
    b'                    # Team is home: favorable = home wins (codes 0,2), unfavorable = away reg loss (code 1)',
    b'                    game_favorable.append({0, 2})',
    b'                    game_unfavorable.append({1})',
    b'                else:',
    b'                    # Team is away: favorable = away wins (codes 1,3), unfavorable = home reg win (code 0)',
    b'                    game_favorable.append({1, 3})',
    b'                    game_unfavorable.append({0})',
    b'            elif rival == "both":',
    b'                home_pct = float(team_playoff_pct[abbrev_to_idx[home]]) if home in abbrev_to_idx else 50',
    b'                away_pct = float(team_playoff_pct[abbrev_to_idx[away]]) if away in abbrev_to_idx else 50',
    b'                if home_pct <= away_pct:',
    b'                    game_favorable.append({1})',
    b'                    game_unfavorable.append({0})',
    b'                else:',
    b'                    game_favorable.append({0})',
    b'                    game_unfavorable.append({1})',
    b'            elif rival == home:',
    b'                game_favorable.append({1})',
    b'                game_unfavorable.append({0})',
    b'            elif rival == away:',
    b'                game_favorable.append({0})',
    b'                game_unfavorable.append({1})',
    b'            else:',
    b'                game_favorable.append({1})',
    b'                game_unfavorable.append({0})',
    b'',
    b'        # Compute favorable row mask: for each game, require outcome in favorable set',
    b'        favorable_mask = np.ones(num_simulations, dtype=np.bool_)',
    b'        unfavorable_mask = np.ones(num_simulations, dtype=np.bool_)',
    b'        for ti in range(n_tonight):',
    b'            fav = game_favorable[ti]',
    b'            unfav = game_unfavorable[ti]',
    b'            outcome_col = all_outcomes[:, ti]',
    b'            if fav is not None:',
    b'                favorable_mask &= np.isin(outcome_col, list(fav))',
    b'                unfavorable_mask &= np.isin(outcome_col, list(unfav))',
    b'',
    b'        team_idx = abbrev_to_idx[abbrev]',
    b'        playoff_col = all_playoffs[:, team_idx]',
    b'',
    b'        best_pct = float(playoff_col[favorable_mask].mean() * 100) if favorable_mask.sum() > 50 else baseline',
    b'        worst_pct = float(playoff_col[unfavorable_mask].mean() * 100) if unfavorable_mask.sum() > 50 else baseline',
    b'',
    b'        best_worst[abbrev] = {',
    b'            "best": round(max(best_pct, baseline), 1),',
    b'            "worst": round(min(worst_pct, baseline), 1),',
    b'            "has_game": abbrev in teams_playing,',
    b'        }',
)

new_line_list = []
# Keep lines before helpers
new_line_list.extend(line_list[:is_rival_start])
# Skip helpers (is_rival_start through sched_end)
# Keep lines from sched_end+1 through best_forced_line-1
new_line_list.extend(line_list[sched_end+1:best_forced_line])
# Insert new block
new_line_list.extend(new_lines)
# Skip old game loop + mask + pct block (best_forced_line through end_marker_line)
# Continue from end_marker_line+1
new_line_list.extend(line_list[end_marker_line+1:])

with open('simulator_np.py', 'wb') as f:
    f.write(b'\n'.join(new_line_list))

print("SUCCESS")
