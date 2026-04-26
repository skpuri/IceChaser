"""
Monte Carlo playoff simulator.
Runs 10,000 simulations of the remaining NHL season to compute playoff odds.

Playoff format:
  - Top 3 teams per division qualify
  - Top 2 remaining teams per conference (wildcards) qualify
  - 8 teams per conference, 16 total
"""

import random
from collections import defaultdict


HOME_WIN_PROB = 0.54  # Home ice advantage

# Points for regulation win, OT win, OT loss
REG_WIN_PTS = 2
OT_WIN_PTS = 2
OT_LOSS_PTS = 1

# ~24% of NHL games go to overtime (NHL average)
OT_PROBABILITY = 0.24


def simulate_game(home_pts_pace, away_pts_pace, home_win_prob=HOME_WIN_PROB):
    """
    Simulate a single game. Returns (home_pts_gained, away_pts_gained).
    Models 3 outcomes:
      - Home wins regulation (home=2, away=0)
      - Away wins regulation (home=0, away=2)
      - OT/SO game — winner gets 2, loser gets 1 (consolation point)
    ~24% of NHL games go to overtime.
    """
    # Adjust win probability by relative team strength (points pace)
    if (home_pts_pace + away_pts_pace) > 0:
        # home_strength is ~0.5 when equal; shift home win prob proportionally
        home_strength = home_pts_pace / (home_pts_pace + away_pts_pace)
        # Scale: equal teams → HOME_WIN_PROB; stronger home team → higher prob
        adjusted_home_prob = home_strength * HOME_WIN_PROB / 0.5
        adjusted_home_prob = max(0.3, min(0.7, adjusted_home_prob))
    else:
        adjusted_home_prob = HOME_WIN_PROB

    home_wins = random.random() < adjusted_home_prob
    goes_to_ot = random.random() < OT_PROBABILITY

    if home_wins:
        home_pts_gain = OT_WIN_PTS   # 2 either way
        away_pts_gain = OT_LOSS_PTS if goes_to_ot else 0
    else:
        home_pts_gain = OT_LOSS_PTS if goes_to_ot else 0
        away_pts_gain = OT_WIN_PTS   # 2 either way

    return home_pts_gain, away_pts_gain


def get_playoff_qualifiers(sim_standings, conference):
    """
    Given a dict of {abbrev: points}, return the 8 playoff qualifiers for a conference.
    Top 3 per division, then 2 wildcards.
    """
    conf_teams = [t for t in sim_standings.values() if t["conference"] == conference]

    divisions = defaultdict(list)
    for team in conf_teams:
        divisions[team["division"]].append(team)

    division_qualifiers = set()
    wildcard_pool = []

    for div_name, div_teams in divisions.items():
        # Sort by points desc, then regulation wins as tiebreaker
        sorted_div = sorted(div_teams, key=lambda x: (x["points"], x["reg_wins"]), reverse=True)
        # Top 3 qualify via division
        for i, team in enumerate(sorted_div):
            if i < 3:
                division_qualifiers.add(team["abbrev"])
            else:
                wildcard_pool.append(team)

    # Sort wildcard pool by points
    wildcard_pool.sort(key=lambda x: (x["points"], x["reg_wins"]), reverse=True)
    wildcards = {t["abbrev"] for t in wildcard_pool[:2]}

    return division_qualifiers | wildcards


def run_simulations(teams, num_simulations=10000):
    """
    Run Monte Carlo simulations.
    
    teams: list of team dicts from nhl_api.parse_standings()
    Returns dict of {teamAbbrev: {"playoff_pct": float, "clinched": bool, "eliminated": bool}}
    """
    if not teams:
        return {}

    # Count playoff appearances
    playoff_counts = defaultdict(int)
    total_sims = num_simulations

    # Organize teams by conference/division
    conferences = defaultdict(lambda: defaultdict(list))
    for team in teams:
        conferences[team["conference"]][team["division"]].append(team)

    # Build a lookup for pace
    team_lookup = {t["teamAbbrev"]: t for t in teams}

    # Build the list of remaining games to simulate
    # We don't have the actual schedule of future games, so we generate synthetic
    # remaining matchups within each conference (inter-conference games too)
    # Simplified: each team plays remaining games split ~50/50 home/away vs
    # random opponents weighted by division

    # Pre-compute max possible points for clinch/elimination detection
    all_abbrevs = [t["teamAbbrev"] for t in teams]

    for sim_idx in range(num_simulations):
        # Start with current points
        sim_pts = {t["teamAbbrev"]: t["points"] for t in teams}
        sim_reg_wins = {t["teamAbbrev"]: t["regulationWins"] for t in teams}
        sim_games_rem = {t["teamAbbrev"]: t["gamesRemaining"] for t in teams}

        # Simulate remaining games
        # We'll do a simplified simulation: pair up teams for their remaining games
        # Each team plays roughly equal home/away
        # Generate game pairs: for each team's remaining games, find an opponent

        # Build game schedule approximation
        # Use a round-robin style within conferences, plus some inter-conference
        game_pairs = []

        # For each conference, simulate games among teams
        for conf_name, conf_divisions in conferences.items():
            conf_teams_list = []
            for div_teams in conf_divisions.values():
                conf_teams_list.extend([t["teamAbbrev"] for t in div_teams])

            # Approximate remaining games: each team has gamesRemaining
            # We'll pair them up until all games are roughly played
            abbrevs = conf_teams_list[:]
            # Create pairs: each team needs ~gamesRemaining games
            # Simple approach: random pairings each round
            avg_remaining = sum(sim_games_rem[a] for a in abbrevs) // (2 * len(abbrevs)) if abbrevs else 0

            for _ in range(max(1, avg_remaining)):
                random.shuffle(abbrevs)
                for i in range(0, len(abbrevs) - 1, 2):
                    game_pairs.append((abbrevs[i], abbrevs[i + 1]))

        # Also add ~20% inter-conference games
        all_team_abbrevs = list(sim_pts.keys())
        num_inter = len(game_pairs) // 5
        for _ in range(num_inter):
            a, b = random.sample(all_team_abbrevs, 2)
            game_pairs.append((a, b))

        # Simulate each game
        for home_abbrev, away_abbrev in game_pairs:
            home_team = team_lookup.get(home_abbrev)
            away_team = team_lookup.get(away_abbrev)
            if not home_team or not away_team:
                continue
            if sim_games_rem.get(home_abbrev, 0) <= 0 and sim_games_rem.get(away_abbrev, 0) <= 0:
                continue

            home_pts_gained, away_pts_gained = simulate_game(
                home_team["pointsPace"],
                away_team["pointsPace"]
            )
            sim_pts[home_abbrev] = sim_pts.get(home_abbrev, 0) + home_pts_gained
            sim_pts[away_abbrev] = sim_pts.get(away_abbrev, 0) + away_pts_gained

            # Track regulation wins (2 pts in a non-OT game: if opponent got 0)
            if home_pts_gained == REG_WIN_PTS and away_pts_gained == 0:
                sim_reg_wins[home_abbrev] = sim_reg_wins.get(home_abbrev, 0) + 1
            elif away_pts_gained == REG_WIN_PTS and home_pts_gained == 0:
                sim_reg_wins[away_abbrev] = sim_reg_wins.get(away_abbrev, 0) + 1

            sim_games_rem[home_abbrev] = max(0, sim_games_rem.get(home_abbrev, 0) - 1)
            sim_games_rem[away_abbrev] = max(0, sim_games_rem.get(away_abbrev, 0) - 1)

        # Build final standings for this simulation
        sim_standings = {}
        for team in teams:
            abbrev = team["teamAbbrev"]
            sim_standings[abbrev] = {
                "abbrev": abbrev,
                "conference": team["conference"],
                "division": team["division"],
                "points": sim_pts.get(abbrev, team["points"]),
                "reg_wins": sim_reg_wins.get(abbrev, team["regulationWins"]),
            }

        # Determine playoff qualifiers for each conference
        for conf_name in ["Eastern", "Western"]:
            qualifiers = get_playoff_qualifiers(sim_standings, conf_name)
            for abbrev in qualifiers:
                playoff_counts[abbrev] += 1

    # Compute results
    results = {}
    for team in teams:
        abbrev = team["teamAbbrev"]
        playoff_pct = (playoff_counts[abbrev] / total_sims) * 100

        # Clinched: >99.5% odds AND clinchIndicator set, or odds essentially 100%
        clinched = playoff_pct >= 99.5 or team.get("clinchIndicator", "") in ["x", "y", "z", "p"]

        # Eliminated: <0.5% odds
        eliminated = playoff_pct <= 0.5

        results[abbrev] = {
            "playoff_pct": round(playoff_pct, 1),
            "clinched": clinched,
            "eliminated": eliminated,
            "sim_count": playoff_counts[abbrev],
        }

    return results


def run_simulations_with_forced_result(teams, forced_home_abbrev, forced_away_abbrev,
                                       forced_winner_abbrev, ot_game=False,
                                       num_simulations=1000):
    """
    Run Monte Carlo simulation with one specific game's result pre-applied.

    forced_winner_abbrev: the team that won the game
    ot_game: if True, the losing team receives 1 consolation point (OT loss)
             if False, the losing team receives 0 points (regulation loss)

    Returns the full sim_results dict (all teams), same format as run_simulations().
    """
    modified_teams = []
    for team in teams:
        t = dict(team)  # shallow copy – safe since we only modify top-level scalars
        abbrev = t["teamAbbrev"]
        if abbrev in (forced_home_abbrev, forced_away_abbrev):
            if abbrev == forced_winner_abbrev:
                t["points"] = t["points"] + 2
                # Only a regulation win if not OT
                if not ot_game:
                    t["regulationWins"] = t.get("regulationWins", 0) + 1
            else:
                # Loser: gets 1 point if OT, 0 if regulation loss
                if ot_game:
                    t["points"] = t["points"] + 1
            # Both teams lose one remaining game regardless of outcome
            t["gamesRemaining"] = max(0, t.get("gamesRemaining", 0) - 1)
        modified_teams.append(t)

    return run_simulations(modified_teams, num_simulations=num_simulations)


def run_simulations_with_multiple_forced(teams, forced_outcomes, num_simulations=2000):
    """
    Run Monte Carlo simulation with multiple specific game results pre-applied.

    forced_outcomes: list of dicts or tuples, each specifying:
      - home: home team abbrev
      - away: away team abbrev
      - winner: winning team abbrev
      - ot_game: bool (default False) — whether loser gets 1 consolation pt

    Returns the full sim_results dict, same format as run_simulations().
    """
    modified_teams = [dict(t) for t in teams]
    abbrev_to_idx = {t["teamAbbrev"]: i for i, t in enumerate(modified_teams)}

    for outcome in forced_outcomes:
        # Accept both dict and tuple/list
        if isinstance(outcome, dict):
            home = outcome["home"]
            away = outcome["away"]
            winner = outcome["winner"]
            ot_game = outcome.get("ot_game", False)
        else:
            home, away, winner = outcome[0], outcome[1], outcome[2]
            ot_game = outcome[3] if len(outcome) > 3 else False

        loser = away if winner == home else home

        for abbrev in [home, away]:
            if abbrev in abbrev_to_idx:
                idx = abbrev_to_idx[abbrev]
                modified_teams[idx]["gamesRemaining"] = max(
                    0, modified_teams[idx].get("gamesRemaining", 0) - 1
                )

        if winner in abbrev_to_idx:
            idx = abbrev_to_idx[winner]
            modified_teams[idx]["points"] += 2
            if not ot_game:
                modified_teams[idx]["regulationWins"] = (
                    modified_teams[idx].get("regulationWins", 0) + 1
                )

        if ot_game and loser in abbrev_to_idx:
            idx = abbrev_to_idx[loser]
            modified_teams[idx]["points"] += 1  # consolation OTL point

    return run_simulations(modified_teams, num_simulations=num_simulations)


def get_division_leaders(teams):
    """Return current division standings."""
    divisions = defaultdict(list)
    for team in teams:
        divisions[team["division"]].append(team)

    result = {}
    for div_name, div_teams in divisions.items():
        sorted_teams = sorted(div_teams, key=lambda x: (x["points"], x["regulationWins"]), reverse=True)
        result[div_name] = sorted_teams

    return result


def get_conference_wildcards(teams, conference):
    """Return the 2 wildcard teams for a conference based on current standings."""
    conf_teams = [t for t in teams if t["conference"] == conference]
    divisions = defaultdict(list)
    for team in conf_teams:
        divisions[team["division"]].append(team)

    division_leaders_abbrevs = set()
    wildcard_pool = []

    for div_name, div_teams in divisions.items():
        sorted_div = sorted(div_teams, key=lambda x: (x["points"], x["regulationWins"]), reverse=True)
        for i, team in enumerate(sorted_div):
            if i < 3:
                division_leaders_abbrevs.add(team["teamAbbrev"])
            else:
                wildcard_pool.append(team)

    wildcard_pool.sort(key=lambda x: (x["points"], x["regulationWins"]), reverse=True)
    return wildcard_pool[:2]
