"""
IceChaser Advanced Features

1. Clinch/elimination scenarios — specific game outcomes that clinch/eliminate teams
2. Projected final standings — expected points, wins, seed from simulations
3. Draft lottery odds for eliminated teams
4. Game night tweet summaries
"""

import numpy as np
from collections import defaultdict


# NHL Draft Lottery probabilities (2024+ format)
# Bottom 16 non-playoff teams, weighted by inverse standing
LOTTERY_ODDS_PCT = [
    25.5, 13.5, 11.5, 9.5, 8.5, 7.5, 6.5, 6.0,
    5.0,  3.5,  3.0,  0.0, 0.0, 0.0, 0.0, 0.0,
]
MAX_MOVE_UP = 10          # 10-spot cap
NUM_LOTTERY_SLOTS = 16    # total non-playoff teams
NUM_DRAWS = 2             # #1 and #2 overall are drawn
DEFAULT_SIMS = 200_000


def compute_clinch_elimination_scenarios(teams, today_games, sim_results, conferences):
    """
    For each bubble team, determine which specific tonight game outcomes
    would clinch or eliminate them.
    
    Returns {abbrev: {"clinch_scenarios": [...], "elimination_scenarios": [...]}}
    """
    results = {}
    
    # Get conference standings
    for conf_name in ["Eastern", "Western"]:
        conf_teams = [t for t in teams if t.get("conference") == conf_name]
        conf_teams.sort(key=lambda t: (-t.get("points", 0), -t.get("regulationWins", t.get("wins", 0))))
        
        if len(conf_teams) < 9:
            continue
        
        # Get tonight's games in this conference
        conf_abbrevs = {t["teamAbbrev"] for t in conf_teams}
        conf_games = [g for g in today_games 
                      if g.get("homeTeamAbbrev") in conf_abbrevs or g.get("awayTeamAbbrev") in conf_abbrevs]
        active_games = [g for g in conf_games 
                        if g.get("gameState", "") not in ("OFF", "FINAL", "OVER")]
        
        if not active_games:
            continue
        
        for team in conf_teams:
            abbrev = team["teamAbbrev"]
            pts = team.get("points", 0)
            gr = team.get("gamesRemaining", 0)
            max_pts = pts + gr * 2
            
            sr = sim_results.get(abbrev, {})
            odds = sr.get("playoff_pct", 0)
            
            # Skip already decided teams
            if sr.get("clinched") or sr.get("eliminated"):
                continue
            if odds >= 99.5 or odds <= 0.05:
                continue
            
            clinch_scenarios = []
            elimination_scenarios = []
            
            # Check each game: what happens if team X wins/loses
            for game in active_games:
                home = game["homeTeamAbbrev"]
                away = game["awayTeamAbbrev"]
                
                # Is this team playing?
                team_playing = abbrev in (home, away)
                
                if team_playing:
                    opponent = away if abbrev == home else home
                    
                    # If this team wins, check if combined with other results could clinch
                    # Simple heuristic: if winning moves best_case above 99%, it's a clinch path
                    best = team.get("best_case_tonight", odds)
                    worst = team.get("worst_case_tonight", odds)
                    
                    if best >= 95 and odds < 95:
                        clinch_scenarios.append({
                            "type": "own_game",
                            "description": f"Win vs {opponent}",
                            "detail": f"A win tonight pushes odds to ~{best:.0f}%",
                            "game": f"{away} @ {home}",
                            "needed_result": f"{abbrev} wins",
                        })
                    
                    if worst <= 5 and odds > 5:
                        elimination_scenarios.append({
                            "type": "own_game",
                            "description": f"Lose to {opponent} in regulation",
                            "detail": f"A regulation loss drops odds to ~{worst:.0f}%",
                            "game": f"{away} @ {home}",
                            "needed_result": f"{abbrev} loses (REG)",
                        })
                else:
                    # Other team's game — check if it matters
                    # Look at scenario data for this team
                    scenarios = team.get("game_scenarios", [])
                    for sc in scenarios:
                        if sc.get("home_team") == home and sc.get("away_team") == away:
                            home_pct = sc.get("if_home_reg_win_pct", sc.get("if_home_wins_pct", odds))
                            away_pct = sc.get("if_away_reg_win_pct", sc.get("if_away_wins_pct", odds))
                            
                            home_delta = home_pct - odds
                            away_delta = away_pct - odds
                            
                            if home_delta > 3:
                                clinch_scenarios.append({
                                    "type": "other_game",
                                    "description": f"{home} beats {away}",
                                    "detail": f"Moves odds from {odds:.1f}% → {home_pct:.1f}% (+{home_delta:.1f}%)",
                                    "game": f"{away} @ {home}",
                                    "needed_result": f"{home} wins",
                                })
                            elif home_delta < -3:
                                elimination_scenarios.append({
                                    "type": "other_game",
                                    "description": f"{home} beats {away}",
                                    "detail": f"Drops odds from {odds:.1f}% → {home_pct:.1f}% ({home_delta:.1f}%)",
                                    "game": f"{away} @ {home}",
                                    "needed_result": f"{home} wins",
                                })
                            
                            if away_delta > 3:
                                clinch_scenarios.append({
                                    "type": "other_game",
                                    "description": f"{away} beats {home}",
                                    "detail": f"Moves odds from {odds:.1f}% → {away_pct:.1f}% (+{away_delta:.1f}%)",
                                    "game": f"{away} @ {home}",
                                    "needed_result": f"{away} wins",
                                })
                            elif away_delta < -3:
                                elimination_scenarios.append({
                                    "type": "other_game",
                                    "description": f"{away} beats {home}",
                                    "detail": f"Drops odds from {odds:.1f}% → {away_pct:.1f}% ({away_delta:.1f}%)",
                                    "game": f"{away} @ {home}",
                                    "needed_result": f"{away} wins",
                                })
            
            if clinch_scenarios or elimination_scenarios:
                # Sort by impact
                clinch_scenarios.sort(key=lambda s: -abs(float(s.get("detail", "0").split("→")[1].split("%")[0].strip()) - odds) if "→" in s.get("detail","") else 0)
                elimination_scenarios.sort(key=lambda s: abs(float(s.get("detail", "0").split("→")[1].split("%")[0].strip()) - odds) if "→" in s.get("detail","") else 0)
                
                results[abbrev] = {
                    "clinch_scenarios": clinch_scenarios[:5],
                    "elimination_scenarios": elimination_scenarios[:5],
                }
    
    return results


def compute_projected_standings(teams, sim_results, what_if_data):
    """
    From the What If data, compute expected final points, wins, and projected seed.
    """
    projections = {}
    
    for team in teams:
        abbrev = team["teamAbbrev"]
        wif = what_if_data.get(abbrev, [])
        
        current_pts = team.get("points", 0)
        current_wins = team.get("wins", 0)
        gr = team.get("gamesRemaining", 0)
        
        if wif:
            # Weighted average from What If table
            total_sims = sum(r.get("times", 0) for r in wif)
            if total_sims > 0:
                exp_add_wins = sum(r["wins"] * r["times"] for r in wif) / total_sims
                exp_add_otl = sum(r.get("otl", 0) * r["times"] for r in wif) / total_sims
                exp_add_losses = sum(r.get("losses", 0) * r["times"] for r in wif) / total_sims
                exp_final_pts = current_pts + exp_add_wins * 2 + exp_add_otl
                exp_final_wins = current_wins + exp_add_wins
                
                # Points range (10th to 90th percentile)
                sorted_wif = sorted(wif, key=lambda r: r.get("final_points", 0))
                cumulative = 0
                p10_pts = sorted_wif[0].get("final_points", 0)
                p90_pts = sorted_wif[-1].get("final_points", 0)
                for r in sorted_wif:
                    cumulative += r["times"]
                    if cumulative >= total_sims * 0.1 and p10_pts == sorted_wif[0].get("final_points", 0):
                        p10_pts = r.get("final_points", 0)
                    if cumulative >= total_sims * 0.9:
                        p90_pts = r.get("final_points", 0)
                        break
                
                projections[abbrev] = {
                    "projected_points": round(exp_final_pts, 1),
                    "projected_wins": round(exp_final_wins, 1),
                    "projected_add_wins": round(exp_add_wins, 1),
                    "projected_add_losses": round(exp_add_losses, 1),
                    "projected_add_otl": round(exp_add_otl, 1),
                    "points_p10": round(p10_pts, 0),
                    "points_p90": round(p90_pts, 0),
                    "projected_record": f"{round(exp_final_wins)}-{round(current_wins + gr - exp_final_wins - exp_add_otl)}-{round(exp_add_otl + team.get('otLosses', 0))}",
                }
        else:
            # Fallback: extrapolate from current pace
            if team.get("gamesPlayed", 0) > 0:
                pace = current_pts / team["gamesPlayed"]
                exp_final_pts = current_pts + gr * pace
                projections[abbrev] = {
                    "projected_points": round(exp_final_pts, 1),
                    "projected_wins": round(current_wins + gr * (current_wins / team["gamesPlayed"]), 1),
                    "points_p10": round(exp_final_pts - 5, 0),
                    "points_p90": round(exp_final_pts + 5, 0),
                }
    
    # Assign projected seeds per conference
    for conf_name in ["Eastern", "Western"]:
        conf_projs = [(a, p) for a, p in projections.items() 
                      if any(t["teamAbbrev"] == a and t.get("conference") == conf_name for t in teams)]
        conf_projs.sort(key=lambda x: -x[1].get("projected_points", 0))
        for rank, (abbrev, proj) in enumerate(conf_projs):
            proj["projected_seed"] = rank + 1
            if rank < 8:
                proj["projected_status"] = "playoff"
            else:
                proj["projected_status"] = "lottery"
    
    return projections


def _run_one_lottery(num_teams, rng):
    """
    Run a single lottery draw, return list where index = lottery seed (0..15)
    and value = final pick number (1..16).
    """
    seeds = list(range(num_teams))  # 0 = worst, num_teams-1 = best non-playoff
    final_pick = [None] * num_teams
    next_non_lottery_pick = 1  # picks 1..NUM_DRAWS come from draws; rest fill in

    for draw in range(NUM_DRAWS):
        target_pick = draw + 1
        eligible = [
            s for s in seeds
            if final_pick[s] is None
            and s + 1 <= target_pick + MAX_MOVE_UP  # within 10-spot cap
        ]
        if not eligible:
            break
        weights = [LOTTERY_ODDS_PCT[s] for s in eligible]
        total = sum(weights)
        if total <= 0:
            winner = min(eligible)
        else:
            winner = rng.choices(eligible, weights=weights, k=1)[0]
        final_pick[winner] = target_pick

    # Fill remaining picks in reverse-standings (seed) order
    remaining_seeds = [s for s in seeds if final_pick[s] is None]
    remaining_seeds.sort()  # worst first
    pick_num = NUM_DRAWS + 1
    for s in remaining_seeds:
        final_pick[s] = pick_num
        pick_num += 1
    return final_pick


def compute_draft_lottery_odds(teams, sim_results, projections, num_sims=DEFAULT_SIMS):
    """
    Drop-in replacement. Returns per-team draft lottery data including a full
    distribution across final picks 1..16.
    """
    import random
    rng = random.Random(42)  # reproducible between runs

    # Build list of lottery-eligible teams (eliminated or low playoff odds).
    lottery_teams = []
    for team in teams:
        abbrev = team["teamAbbrev"]
        sr = sim_results.get(abbrev, {})
        odds = sr.get("playoff_pct", 0)
        if sr.get("eliminated") or odds < 5:
            proj = projections.get(abbrev, {})
            lottery_teams.append({
                "abbrev": abbrev,
                "projected_points": proj.get("projected_points", team.get("points", 0)),
                "current_points": team.get("points", 0),
            })
    if not lottery_teams:
        return {}

    # Worst projected points first = seed 0
    lottery_teams.sort(key=lambda t: t["projected_points"])
    n = min(len(lottery_teams), NUM_LOTTERY_SLOTS)

    # Run Monte Carlo
    pick_counts = [[0] * (NUM_LOTTERY_SLOTS + 1) for _ in range(n)]
    for _ in range(num_sims):
        final = _run_one_lottery(n, rng)
        for seed_idx, pk in enumerate(final):
            if pk is not None and 1 <= pk <= NUM_LOTTERY_SLOTS:
                pick_counts[seed_idx][pk] += 1

    results = {}
    for i, lt in enumerate(lottery_teams[:n]):
        counts = pick_counts[i]
        pick_dist = {str(p): round(100.0 * counts[p] / num_sims, 2)
                     for p in range(1, NUM_LOTTERY_SLOTS + 1)}
        first_pct = pick_dist["1"]
        top_3_pct = round(pick_dist["1"] + pick_dist["2"] + pick_dist["3"], 2)
        ev = sum(p * counts[p] for p in range(1, NUM_LOTTERY_SLOTS + 1)) / num_sims
        results[lt["abbrev"]] = {
            "lottery_position": i + 1,
            "first_pick_pct": first_pct,
            "top_3_pct": top_3_pct,
            "projected_pick": round(ev, 1),
            "pick_distribution": pick_dist,
            "total_lottery_teams": n,
            "base_odds_pct": LOTTERY_ODDS_PCT[i] if i < len(LOTTERY_ODDS_PCT) else 0.0,
        }
    return results


if __name__ == "__main__":
    # Quick sanity test with fake data for 16 teams
    import random
    teams = [{"teamAbbrev": f"T{i:02d}", "points": 100 - i * 3} for i in range(16)]
    sim_results = {t["teamAbbrev"]: {"playoff_pct": 0, "eliminated": True} for t in teams}
    projections = {t["teamAbbrev"]: {"projected_points": t["points"]} for t in teams}
    out = compute_draft_lottery_odds(teams, sim_results, projections, num_sims=50000)
    for ab, d in out.items():
        print(f"{ab}  seed={d['lottery_position']}  #1={d['first_pick_pct']}%  "
              f"top3={d['top_3_pct']}%  EV={d['projected_pick']}")


def generate_game_tweets(teams, today_games, sim_results, team_scenarios=None):
    """
    Generate tweet-length summaries for each game result.
    """
    tweets = []
    
    team_names = {t["teamAbbrev"]: t.get("teamCommonName", t.get("teamName", t["teamAbbrev"])) for t in teams}
    
    for game in today_games:
        home = game.get("homeTeamAbbrev", "")
        away = game.get("awayTeamAbbrev", "")
        state = game.get("gameState", "")
        
        if state not in ("OFF", "FINAL"):
            continue
        
        h_score = game.get("homeScore", 0)
        a_score = game.get("awayScore", 0)
        winner = home if h_score > a_score else away
        loser = away if h_score > a_score else home
        winner_name = team_names.get(winner, winner)
        loser_name = team_names.get(loser, loser)
        
        w_sr = sim_results.get(winner, {})
        l_sr = sim_results.get(loser, {})
        w_odds = w_sr.get("playoff_pct", 0)
        l_odds = l_sr.get("playoff_pct", 0)
        
        # Build tweet
        score_str = f"{max(h_score, a_score)}-{min(h_score, a_score)}"
        tweet = f"🏒 FINAL: {winner_name} beat {loser_name} {score_str}.\n"
        
        if w_odds < 99 and w_odds > 1:
            tweet += f"📈 {winner}: {w_odds:.1f}% playoff odds\n"
        if l_odds < 99 and l_odds > 1:
            tweet += f"📉 {loser}: {l_odds:.1f}% playoff odds\n"
        
        if w_sr.get("clinched"):
            tweet += f"✅ {winner} have clinched!\n"
        if l_sr.get("eliminated"):
            tweet += f"❌ {loser} eliminated.\n"
        
        tweet += "#NHL #IceChaser"
        
        tweets.append({
            "game": f"{away} @ {home}",
            "winner": winner,
            "loser": loser,
            "tweet": tweet.strip(),
            "chars": len(tweet.strip()),
        })
    
    return tweets
