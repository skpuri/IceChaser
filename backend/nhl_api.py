"""NHL API client using api-web.nhle.com (free, no auth required)"""

import requests
from datetime import datetime, date, timezone, timedelta

# PST timezone (UTC-8) — all date calculations use the West Coast fan's local time
PST = timezone(timedelta(hours=-8))

BASE_URL = "https://api-web.nhle.com/v1"

HEADERS = {
    "User-Agent": "IceChaser/1.0 NHL Playoff Odds Tracker"
}


def get_standings():
    """Fetch current NHL standings."""
    url = f"{BASE_URL}/standings/now"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_schedule_now():
    """Fetch today's schedule."""
    url = f"{BASE_URL}/schedule/now"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_schedule_by_date(date_str):
    """Fetch schedule for a specific date (YYYY-MM-DD)."""
    url = f"{BASE_URL}/schedule/{date_str}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def parse_standings(raw):
    """
    Parse raw standings JSON into a list of team dicts.
    Returns list of dicts with keys:
      teamAbbrev, teamName, conference, division,
      gamesPlayed, wins, losses, otLosses, points,
      pointPctg, regulationWins, goalsFor, goalsAgainst,
      winStreak, pointsPercentage, gamesRemaining
    """
    teams = []
    standings_data = raw.get("standings", [])

    for entry in standings_data:
        # NHL regular season is 82 games
        games_played = entry.get("gamesPlayed", 0)
        games_remaining = 82 - games_played
        points = entry.get("points", 0)
        # Points pace over 82 games
        points_pace = (points / games_played * 82) if games_played > 0 else 0

        team = {
            "teamAbbrev": entry.get("teamAbbrev", {}).get("default", ""),
            "teamName": entry.get("teamName", {}).get("default", ""),
            "teamCommonName": entry.get("teamCommonName", {}).get("default", ""),
            "conference": entry.get("conferenceName", ""),
            "division": entry.get("divisionName", ""),
            "gamesPlayed": games_played,
            "wins": entry.get("wins", 0),
            "losses": entry.get("losses", 0),
            "otLosses": entry.get("otLosses", 0),
            "points": points,
            "regulationWins": entry.get("regulationWins", 0),
            "goalsFor": entry.get("goalFor", 0),
            "goalsAgainst": entry.get("goalAgainst", 0),
            "pointsPctg": entry.get("pointPctg", 0.0),
            "gamesRemaining": games_remaining,
            "pointsPace": round(points_pace, 1),
            "divisionSequence": entry.get("divisionSequence", 99),
            "wildcardSequence": entry.get("wildcardSequence", 99),
            "conferenceSequence": entry.get("conferenceSequence", 99),
            "clinchIndicator": entry.get("clinchIndicator", ""),
            "l10Wins": entry.get("l10Wins", 0),
            "l10Losses": entry.get("l10Losses", 0),
            "l10OtLosses": entry.get("l10OtLosses", 0),
            "streakCode": entry.get("streakCode", ""),
            "streakCount": entry.get("streakCount", 0),
        }
        teams.append(team)

    return teams


def parse_schedule(raw, today_only=True):
    """
    Parse schedule JSON into a list of game dicts.
    Returns list with home/away team info and game status.
    If today_only=True, only returns games for today's date.

    "Today" is determined by gameWeek[0].date from the API response, since the
    NHL API always puts the current game-day first (which may differ from UTC
    date near midnight). Falls back to UTC date if gameWeek is empty.
    """
    games = []
    game_week = raw.get("gameWeek", [])

    # Use the first day the API returns as "today" — more reliable than UTC
    # clock near midnight when yesterday's games are still LIVE.
    if game_week:
        today_str = game_week[0].get("date", datetime.now(PST).strftime('%Y-%m-%d'))
    else:
        today_str = datetime.now(PST).strftime('%Y-%m-%d')

    for day in game_week:
        # Filter to today only if requested
        if today_only and day.get("date", "") != today_str:
            continue
        for game in day.get("games", []):
            home_team = game.get("homeTeam", {})
            away_team = game.get("awayTeam", {})

            game_dict = {
                "gameId": game.get("id", 0),
                "gameDate": day["date"],  # date is on the day object, not the game object
                "gameTime": game.get("startTimeUTC", ""),
                "gameState": game.get("gameState", ""),
                "homeTeamAbbrev": home_team.get("abbrev", ""),
                "homeTeamName": home_team.get("commonName", {}).get("default", ""),
                "homeScore": home_team.get("score", 0),
                "awayTeamAbbrev": away_team.get("abbrev", ""),
                "awayTeamName": away_team.get("commonName", {}).get("default", ""),
                "awayScore": away_team.get("score", 0),
                "venue": game.get("venue", {}).get("default", ""),
                "period": game.get("periodDescriptor", {}).get("number", 0),
                "isPlayoff": game.get("gameType", 2) == 3,
            }
            games.append(game_dict)

    return games


def enrich_live_game_clock(games):
    """
    For LIVE/CRIT games, fetch clock and period info from gamecenter API.
    Adds: timeRemaining, periodType, inIntermission, secondsRemaining.
    """
    for game in games:
        if game.get('gameState') not in ('LIVE', 'CRIT'):
            continue
        game_id = game.get('gameId', '')
        if not game_id:
            continue
        try:
            url = f"https://api-web.nhle.com/v1/gamecenter/{game_id}/landing"
            resp = requests.get(url, headers=HEADERS, timeout=8)
            d = resp.json()
            clock = d.get('clock', {})
            period_desc = d.get('periodDescriptor', {})
            game['timeRemaining'] = clock.get('timeRemaining') or ''
            game['secondsRemaining'] = clock.get('secondsRemaining') or None
            game['inIntermission'] = clock.get('inIntermission') or False
            game['periodType'] = period_desc.get('periodType') or 'REG'
            game['period'] = period_desc.get('number') or game.get('period', 0)
        except Exception:
            pass
    return games


def get_schedule_with_fallback():
    """
    Fetch today's schedule, with fallback to date-specific endpoint if
    schedule/now returns no games.
    Returns the raw JSON that has games for today.
    """
    today_str = datetime.now(PST).strftime('%Y-%m-%d')

    # Try /schedule/now first
    raw = get_schedule_now()
    games = parse_schedule(raw, today_only=True)

    # If schedule/now returned games but they're ALL finished (OFF/FINAL),
    # and the date doesn't match today's UTC date, the API is still showing
    # yesterday's late games. Fetch today's actual date instead.
    all_done = games and all(g.get("gameState") in ("OFF", "FINAL") for g in games)
    api_date = raw.get("gameWeek", [{}])[0].get("date", "") if raw.get("gameWeek") else ""
    if (not games or (all_done and api_date != today_str)):
        print(f"   ⚠ schedule/now shows {'all finished games from ' + api_date if all_done else 'no games'} — fetching /schedule/{today_str}")
        raw = get_schedule_by_date(today_str)
        games = parse_schedule(raw, today_only=True)
        if not games:
            print(f"   ⚠ No games found for {today_str} either")

    return raw, games


def get_today_games():
    """Get today's games parsed, with live clock data enriched."""
    _raw, games = get_schedule_with_fallback()
    return enrich_live_game_clock(games)


def get_all_data():
    """Fetch standings and today's schedule in one call, with live clock data."""
    standings_raw = get_standings()
    _schedule_raw, games = get_schedule_with_fallback()
    games = enrich_live_game_clock(games)
    teams = parse_standings(standings_raw)
    return teams, games


def get_remaining_schedule():
    """
    Fetch all remaining games in the regular season.
    Returns list of (home_abbrev, away_abbrev) tuples for FUT/PRE games.
    """
    from datetime import timedelta
    
    all_games = []
    seen_game_ids = set()
    
    # Start from today, page through weeks until no more FUT games
    current_date = datetime.now(PST).date()
    end_date = current_date + timedelta(days=30)  # ~1 month should cover remaining season
    
    d = current_date
    while d <= end_date:
        try:
            date_str = d.strftime("%Y-%m-%d")
            url = f"{BASE_URL}/schedule/{date_str}"
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                d += timedelta(days=7)
                continue
            data = resp.json()
            
            found_future = False
            for day in data.get("gameWeek", []):
                for game in day.get("games", []):
                    game_id = game.get("id", 0)
                    if game_id in seen_game_ids:
                        continue
                    seen_game_ids.add(game_id)
                    
                    state = game.get("gameState", "")
                    if state in ("FUT", "PRE"):
                        found_future = True
                        home = game["homeTeam"]["abbrev"]
                        away = game["awayTeam"]["abbrev"]
                        all_games.append((home, away))
            
            if not found_future and d > current_date + timedelta(days=14):
                break  # No more future games
            
            d += timedelta(days=7)
        except Exception as e:
            d += timedelta(days=7)
            continue
    
    return all_games


def get_tomorrow_games():
    """
    Fetch tomorrow's games using the actual next calendar day (not NHL 'now' date),
    then find the first day in the schedule API response that has games.
    This avoids the midnight-flip bug where get_schedule_now() returns yesterday's
    date during the morning hours before the NHL calendar flips.
    """
    from datetime import timedelta
    # Use actual next calendar day from UTC — this is what users mean by 'tomorrow'
    actual_tomorrow = (datetime.now(PST) + timedelta(days=1)).strftime("%Y-%m-%d")
    # The NHL schedule API returns a week window starting from some date.
    # Try fetching the actual_tomorrow date; the API will return a window starting
    # from that date. We pick the first day in the window that has games.
    try:
        raw = get_schedule_by_date(actual_tomorrow)
        game_week = raw.get("gameWeek", [])
        # The API returns days starting from the requested date.
        # Use the date from the response's first day as 'tomorrow'.
        # This is the NHL's interpretation which may differ from our UTC date.
        tomorrow_from_api = actual_tomorrow
        for day in game_week:
            if day.get("games"):
                tomorrow_from_api = day["date"]
                break
        tomorrow = tomorrow_from_api
    except Exception:
        tomorrow = actual_tomorrow
    try:
        raw = get_schedule_by_date(tomorrow)
        games = []
        for day in raw.get("gameWeek", []):
            if day["date"] != tomorrow:
                continue
            for g in day.get("games", []):
                games.append({
                    "gameId": g.get("id", 0),
                    "gameDate": tomorrow,
                    "gameTime": g.get("startTimeUTC", ""),
                    "gameState": g.get("gameState", "FUT"),
                    "homeTeamAbbrev": g["homeTeam"]["abbrev"],
                    "homeTeamName": g["homeTeam"].get("placeName", {}).get("default", g["homeTeam"]["abbrev"]),
                    "awayTeamAbbrev": g["awayTeam"]["abbrev"],
                    "awayTeamName": g["awayTeam"].get("placeName", {}).get("default", g["awayTeam"]["abbrev"]),
                    "homeScore": 0,
                    "awayScore": 0,
                    "venue": g.get("venue", {}).get("default", ""),
                })
        return games
    except Exception as e:
        print(f"Error fetching tomorrow's games: {e}")
        return []
