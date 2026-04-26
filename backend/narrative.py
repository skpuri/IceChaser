"""
Generate human-readable narrative text about the NHL playoff race.
"""

from collections import defaultdict


def get_headline(teams, sim_results, today_games):
    """Generate the main headline narrative."""
    if not teams:
        return "NHL playoff race data is being updated."

    # Find teams on the bubble (20-80% odds)
    bubble_teams = [
        t for t in teams
        if 20 <= sim_results.get(t["teamAbbrev"], {}).get("playoff_pct", 0) <= 80
    ]

    # Count clinched and eliminated
    clinched = [t for t in teams if sim_results.get(t["teamAbbrev"], {}).get("clinched", False)]
    eliminated = [t for t in teams if sim_results.get(t["teamAbbrev"], {}).get("eliminated", False)]
    
    # Check if all games are final
    finished = [g for g in today_games if g.get("gameState") in ("FINAL", "OFF")]
    active = [g for g in today_games if g.get("gameState") not in ("FINAL", "OFF")]
    all_final = len(finished) == len(today_games) and len(today_games) > 0
    
    if all_final:
        # Post-game summary headline
        spots_remaining = 16 - len(clinched)
        if spots_remaining <= 0:
            return (f"🏆 All 16 playoff spots are locked in! "
                    f"The postseason picture is set.")
        
        if len(bubble_teams) > 0:
            closest = sorted(bubble_teams, key=lambda t: abs(sim_results.get(t["teamAbbrev"], {}).get("playoff_pct", 50) - 50))
            closest_name = closest[0]["teamCommonName"]
            closest_pct = sim_results.get(closest[0]["teamAbbrev"], {}).get("playoff_pct", 50)
            return (f"📊 Tonight's games are in the books. {len(clinched)} teams have clinched, "
                    f"{spots_remaining} spots remain. {closest_name} sit right on the bubble at {closest_pct:.0f}%. "
                    f"{len(bubble_teams)} teams are still in the fight.")
        else:
            return (f"📊 All {len(finished)} games are final. {len(clinched)} clinched, "
                    f"{len(eliminated)} eliminated. The playoff picture is sharpening.")
    
    # During active games
    if active:
        live = [g for g in active if g.get("gameState") in ("LIVE", "CRIT")]
        if live:
            high_impact = _find_high_impact_games(live, teams, sim_results)
            if high_impact:
                g = high_impact[0]
                return (f"🔴 LIVE — {len(live)} games in progress. "
                        f"{g['awayTeamAbbrev']} @ {g['homeTeamAbbrev']} has major playoff implications. "
                        f"{len(bubble_teams)} teams watching closely.")
    
    # Pre-game
    if today_games:
        high_impact = _find_high_impact_games(today_games, teams, sim_results)
        if high_impact:
            g = high_impact[0]
            return (f"⚔️ Tonight is huge for the playoff race. "
                    f"{g['awayTeamAbbrev']} @ {g['homeTeamAbbrev']} could shake up the standings — "
                    f"both teams are fighting for their playoff lives.")

    if len(bubble_teams) > 5:
        teams_str = ", ".join(t["teamCommonName"] for t in bubble_teams[:3])
        return (f"🔥 It's a playoff pile-up — {len(bubble_teams)} teams are still fighting for their postseason lives. "
                f"{teams_str} and others face must-win situations as the season enters its final stretch.")

    # Fallback
    total_games_rem = sum(t.get("gamesRemaining", 0) for t in teams) // 2
    return (f"🏒 The NHL playoff race heats up with approximately {total_games_rem} games remaining. "
            f"Every point counts as teams battle for the 16 postseason spots.")


def get_biggest_movers(teams, sim_results, previous_results=None):
    """Generate biggest movers narrative."""
    if not previous_results:
        # No previous data — report on current bubble
        bubble = sorted(
            [t for t in teams if 20 <= sim_results.get(t["teamAbbrev"], {}).get("playoff_pct", 0) <= 80],
            key=lambda x: sim_results.get(x["teamAbbrev"], {}).get("playoff_pct", 0),
            reverse=True
        )
        if bubble:
            names = ", ".join(
                f"{t['teamCommonName']} ({sim_results.get(t['teamAbbrev'], {}).get('playoff_pct', 0):.0f}%)"
                for t in bubble[:4]
            )
            return f"📊 Bubble watch: {names} — all with meaningful playoff odds heading into tonight."
        return "📊 Playoff odds are stabilizing as teams separate in the standings."

    # Compute deltas
    deltas = []
    for team in teams:
        abbrev = team["teamAbbrev"]
        curr = sim_results.get(abbrev, {}).get("playoff_pct", 0)
        prev = previous_results.get(abbrev, {}).get("playoff_pct", curr)
        delta = curr - prev
        if abs(delta) >= 1.0:
            deltas.append((team, delta))

    if not deltas:
        return "📊 Steady state — no major shifts in playoff odds today."

    deltas.sort(key=lambda x: abs(x[1]), reverse=True)
    
    # Separate risers and fallers
    risers = [(t, d) for t, d in deltas if d > 0]
    fallers = [(t, d) for t, d in deltas if d < 0]
    
    parts = []
    for team, delta in risers[:3]:
        parts.append(f"{team['teamCommonName']} ↑{abs(delta):.0f}%")
    for team, delta in fallers[:3]:
        parts.append(f"{team['teamCommonName']} ↓{abs(delta):.0f}%")

    return f"📈 Biggest movers today: {', '.join(parts)}"


def get_bubble_watch(teams, sim_results):
    """Generate bubble watch narrative for teams between 20-80%."""
    bubble = [
        t for t in teams
        if 20 <= sim_results.get(t["teamAbbrev"], {}).get("playoff_pct", 0) <= 80
    ]
    bubble.sort(key=lambda x: sim_results.get(x["teamAbbrev"], {}).get("playoff_pct", 0), reverse=True)

    if not bubble:
        # Season might be mostly decided
        almost_in = [
            t for t in teams
            if sim_results.get(t["teamAbbrev"], {}).get("playoff_pct", 0) > 80
            and not sim_results.get(t["teamAbbrev"], {}).get("clinched", False)
        ]
        if almost_in:
            names = ", ".join(t["teamCommonName"] for t in almost_in[:3])
            return f"🎯 Nearly there: {names} are on the doorstep of clinching a playoff berth."
        return "✅ The playoff picture is becoming clear — most spots are locked up."

    narratives = []
    for team in bubble[:3]:
        abbrev = team["teamAbbrev"]
        pct = sim_results.get(abbrev, {}).get("playoff_pct", 0)
        rem = team.get("gamesRemaining", 0)

        # Estimate wins needed (rough: need ~95 pts to make playoffs historically)
        current_pts = team.get("points", 0)
        pts_needed = max(0, 95 - current_pts)
        wins_needed = (pts_needed + 1) // 2  # rough estimate

        if pct > 60:
            narratives.append(
                f"{team['teamCommonName']} ({pct:.0f}%) are in the driver's seat "
                f"with {rem} games left."
            )
        elif pct > 40:
            narratives.append(
                f"{team['teamCommonName']} ({pct:.0f}%) are right on the bubble — "
                f"they need a strong push over the final {rem} games."
            )
        else:
            narratives.append(
                f"{team['teamCommonName']} ({pct:.0f}%) are running out of runway — "
                f"they need roughly {wins_needed} more wins to stay alive."
            )

    return "🫧 Bubble Watch: " + " | ".join(narratives)


def get_tonight_stakes(today_games, teams, sim_results):
    """Generate narrative about tonight's games — both finished and upcoming."""
    if not today_games:
        return "🌙 No games scheduled tonight — teams rest up for tomorrow's battles."

    # Split into finished and active/upcoming
    finished = [g for g in today_games if g.get("gameState") in ("FINAL", "OFF")]
    active = [g for g in today_games if g.get("gameState") not in ("FINAL", "OFF")]
    
    narratives = []
    
    # Report key finished games
    if finished:
        key_results = []
        for game in finished:
            home = game["homeTeamAbbrev"]
            away = game["awayTeamAbbrev"]
            home_pct = sim_results.get(home, {}).get("playoff_pct", 100)
            away_pct = sim_results.get(away, {}).get("playoff_pct", 100)
            hs = game.get("homeScore", 0)
            as_ = game.get("awayScore", 0)
            
            # Only highlight games involving bubble/fringe teams
            if (home_pct <= 85 and not sim_results.get(home, {}).get("clinched")) or \
               (away_pct <= 85 and not sim_results.get(away, {}).get("clinched")):
                winner = home if hs > as_ else away
                loser = away if hs > as_ else home
                winner_name = (_find_team(teams, winner) or {}).get("teamCommonName", winner)
                loser_name = (_find_team(teams, loser) or {}).get("teamCommonName", loser)
                winner_pct = sim_results.get(winner, {}).get("playoff_pct", 0)
                key_results.append((abs(winner_pct - 50), winner_name, loser_name, winner_pct, hs, as_, home, away))
        
        key_results.sort(key=lambda x: x[0])  # closest to 50% = most interesting
        for _, winner_name, loser_name, wpct, hs, as_, home, away in key_results[:2]:
            score_str = f"{as_}-{hs}" if away != home else ""
            narratives.append(
                f"✅ {winner_name} beat {loser_name} — now at {wpct:.0f}% playoff odds."
            )
    
    # Report upcoming/live games
    high_impact = _find_high_impact_games(active, teams, sim_results)
    if high_impact:
        for game in high_impact[:2]:
            home_pct = sim_results.get(game["homeTeamAbbrev"], {}).get("playoff_pct", 50)
            away_pct = sim_results.get(game["awayTeamAbbrev"], {}).get("playoff_pct", 50)
            home_team = _find_team(teams, game["homeTeamAbbrev"])
            away_team = _find_team(teams, game["awayTeamAbbrev"])
            home_name = home_team["teamCommonName"] if home_team else game["homeTeamAbbrev"]
            away_name = away_team["teamCommonName"] if away_team else game["awayTeamAbbrev"]
            
            is_live = game.get("gameState") in ("LIVE", "CRIT")
            prefix = "🔴 LIVE:" if is_live else "⚡"
            
            narratives.append(
                f"{prefix} {away_name} @ {home_name} — "
                f"{'both teams still fighting' if home_pct < 85 and away_pct < 85 else 'playoff implications on the line'}."
            )
    
    remaining = len(active) - len(high_impact[:2])
    if remaining > 0:
        narratives.append(f"Plus {remaining} more game{'s' if remaining != 1 else ''} still to play.")
    
    if not narratives:
        return f"🏒 All {len(finished)} games are final tonight."
    
    return " ".join(narratives)


def _find_high_impact_games(today_games, teams, sim_results):
    """Find games involving bubble teams."""
    high_impact = []
    for game in today_games:
        home_pct = sim_results.get(game["homeTeamAbbrev"], {}).get("playoff_pct", 100)
        away_pct = sim_results.get(game["awayTeamAbbrev"], {}).get("playoff_pct", 100)
        # High impact if either team is in the 15-85% range
        if (15 <= home_pct <= 85) or (15 <= away_pct <= 85):
            impact_score = (
                (50 - abs(home_pct - 50)) / 50 +
                (50 - abs(away_pct - 50)) / 50
            )
            high_impact.append((impact_score, game))

    high_impact.sort(key=lambda x: x[0], reverse=True)
    return [g for _, g in high_impact]


def _find_team(teams, abbrev):
    """Find team by abbreviation."""
    for team in teams:
        if team["teamAbbrev"] == abbrev:
            return team
    return None


def generate_all_narratives(teams, sim_results, today_games, previous_results=None):
    """Generate all narrative strings and return as dict."""
    return {
        "headline": get_headline(teams, sim_results, today_games),
        "biggest_movers": get_biggest_movers(teams, sim_results, previous_results),
        "bubble_watch": get_bubble_watch(teams, sim_results),
        "tonight_stakes": get_tonight_stakes(today_games, teams, sim_results),
    }
