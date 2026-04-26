"""
IceChaser Static Page Generator

Generates pre-rendered HTML pages from playoff_odds.json for SEO.
Google sees full content without executing JavaScript.

Pages generated:
  /nhl/playoff-odds/index.html — main standings page
  /nhl/teams/{abbrev}/index.html — per-team deep dive
"""

import json
import os
from datetime import datetime, timezone

DATA_PATH = "/var/www/icechaser/data/playoff_odds.json"
OUTPUT_DIR = "/var/www/icechaser"
NHL_LOGO_BASE = "https://assets.nhle.com/logos/nhl/svg/"

TEAM_FULL_NAMES = {
    "ANA": "Anaheim Ducks", "ARI": "Arizona Coyotes", "BOS": "Boston Bruins",
    "BUF": "Buffalo Sabres", "CGY": "Calgary Flames", "CAR": "Carolina Hurricanes",
    "CHI": "Chicago Blackhawks", "COL": "Colorado Avalanche", "CBJ": "Columbus Blue Jackets",
    "DAL": "Dallas Stars", "DET": "Detroit Red Wings", "EDM": "Edmonton Oilers",
    "FLA": "Florida Panthers", "LAK": "Los Angeles Kings", "MIN": "Minnesota Wild",
    "MTL": "Montreal Canadiens", "NSH": "Nashville Predators", "NJD": "New Jersey Devils",
    "NYI": "New York Islanders", "NYR": "New York Rangers", "OTT": "Ottawa Senators",
    "PHI": "Philadelphia Flyers", "PIT": "Pittsburgh Penguins", "SJS": "San Jose Sharks",
    "SEA": "Seattle Kraken", "STL": "St. Louis Blues", "TBL": "Tampa Bay Lightning",
    "TOR": "Toronto Maple Leafs", "UTA": "Utah Hockey Club", "VAN": "Vancouver Canucks",
    "VGK": "Vegas Golden Knights", "WSH": "Washington Capitals", "WPG": "Winnipeg Jets",
}


def load_data():
    with open(DATA_PATH) as f:
        return json.load(f)


def html_head(title, description, canonical, og_title=None):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <!-- Google Analytics 4 -->
  <script async src="https://www.googletagmanager.com/gtag/js?id=G-Z1EX23JWNK"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){{dataLayer.push(arguments);}}
    gtag('js', new Date());
    gtag('config', 'G-Z1EX23JWNK');
  </script>

  <meta name="description" content="{description}">
  <link rel="canonical" href="{canonical}">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{canonical}">
  <meta property="og:title" content="{og_title or title}">
  <meta property="og:description" content="{description}">
  <meta property="og:site_name" content="IceChaser">
  <meta property="og:image" content="https://icechaser.com/data/og_image.png">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:image" content="https://icechaser.com/data/og_image.png">
  <meta name="twitter:title" content="{og_title or title}">
  <meta name="twitter:description" content="{description}">
  <link rel="stylesheet" href="/style.css">
  <style>
    .static-container {{ max-width: 900px; margin: 0 auto; padding: 20px; }}
    .static-header {{ text-align: center; padding: 20px 0; }}
    .static-header h1 {{ font-size: 1.8rem; }}
    .static-header p {{ color: #8b949e; margin-top: 4px; }}
    .team-table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
    .team-table th {{ text-align: left; padding: 8px 12px; border-bottom: 2px solid #30363d; color: #8b949e; font-size: 0.85rem; }}
    .team-table td {{ padding: 8px 12px; border-bottom: 1px solid #21262d; }}
    .team-table tr:hover {{ background: rgba(255,255,255,0.03); }}
    .odds-high {{ color: #3fb950; }}
    .odds-mid {{ color: #d2992a; }}
    .odds-low {{ color: #f85149; }}
    .team-link {{ color: #58a6ff; text-decoration: none; }}
    .team-link:hover {{ text-decoration: underline; }}
    .status-tag {{ font-size: 0.75rem; padding: 1px 6px; border-radius: 6px; font-weight: 600; }}
    .tag-clinched {{ background: rgba(63,185,80,0.15); color: #3fb950; }}
    .tag-eliminated {{ background: rgba(248,81,73,0.15); color: #f85149; }}
    .back-link {{ display: inline-block; margin-bottom: 16px; color: #58a6ff; text-decoration: none; }}
    .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; margin: 16px 0; }}
    .stat-card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px; text-align: center; }}
    .stat-value {{ font-size: 1.8rem; font-weight: 800; }}
    .stat-label {{ color: #8b949e; font-size: 0.8rem; margin-top: 4px; }}
    .wif-table {{ width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 0.9rem; }}
    .wif-table th {{ padding: 6px 10px; text-align: center; border-bottom: 2px solid #30363d; color: #8b949e; }}
    .wif-table td {{ padding: 6px 10px; text-align: center; border-bottom: 1px solid #21262d; }}
    .scenario-section {{ margin: 24px 0; }}
    .scenario-game {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px; margin: 8px 0; }}
    .nav-links {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 16px 0; }}
    .nav-links a {{ background: #161b22; border: 1px solid #30363d; padding: 4px 12px; border-radius: 16px; color: #c9d1d9; text-decoration: none; font-size: 0.85rem; }}
    .nav-links a:hover {{ border-color: #58a6ff; color: #58a6ff; }}
    .live-cta {{ text-align: center; margin: 24px 0; padding: 16px; background: #161b22; border: 1px solid #30363d; border-radius: 8px; }}
    .live-cta a {{ color: #58a6ff; font-weight: 600; text-decoration: none; font-size: 1.1rem; }}
  </style>
</head>
<body>"""


def html_footer(generated_at):
    return f"""
  <footer style="text-align:center;padding:24px;color:#8b949e;font-size:0.8rem;">
    <p>IceChaser uses Elo-rated Monte Carlo simulation (500,000 runs). Data from NHL API.</p>
    <p>Last updated: {generated_at} · <a href="/METHODOLOGY.md" style="color:#58a6ff">Methodology</a></p>
  </footer>
</body>
</html>"""


def odds_class(pct):
    if pct >= 75: return "odds-high"
    if pct >= 25: return "odds-mid"
    return "odds-low"


def generate_main_page(data):
    """Generate /nhl/playoff-odds/index.html"""
    teams = data["teams"]
    gen_at = data["generated_at"]
    
    clinched = sum(1 for t in teams if t.get("clinched"))
    eliminated = sum(1 for t in teams if t.get("eliminated"))
    bubble = sum(1 for t in teams if 20 < t.get("playoffOdds", 0) < 80)
    
    title = f"NHL Playoff Odds — {clinched} Clinched, {bubble} On the Bubble | IceChaser"
    desc = f"Real-time NHL playoff probabilities from 500,000 Monte Carlo simulations. {clinched} teams clinched, {eliminated} eliminated, {bubble} on the bubble. Updated every 20 minutes."
    
    html = html_head(title, desc, "https://icechaser.com/nhl/playoff-odds")
    
    html += """
  <div class="static-container">
    <div class="static-header">
      <h1>🏒 NHL Playoff Odds</h1>
      <p>Elo-Rated Monte Carlo Simulation · 500,000 Runs</p>
    </div>
    
    <div class="live-cta">
      <a href="/">View Live Interactive Dashboard →</a>
    </div>
    
    <div class="nav-links">"""
    
    for t in sorted(teams, key=lambda x: x.get("teamAbbrev", "")):
        abbrev = t["teamAbbrev"]
        name = TEAM_FULL_NAMES.get(abbrev, abbrev)
        html += f'\n      <a href="/nhl/teams/{abbrev.lower()}">{name}</a>'
    
    html += """
    </div>"""
    
    for conf_name in ["Eastern", "Western"]:
        conf = data.get("conferences", {}).get(conf_name, {})
        html += f'\n    <h2>{"🔵" if conf_name == "Eastern" else "🟣"} {conf_name} Conference</h2>'
        
        for div_name, div_teams in conf.get("divisions", {}).items():
            html += f'\n    <h3>{div_name} Division</h3>'
            html += '\n    <table class="team-table"><thead><tr><th>Team</th><th>Record</th><th>PTS</th><th>Playoff %</th><th>Status</th></tr></thead><tbody>'
            
            for t in div_teams:
                abbrev = t["teamAbbrev"]
                name = TEAM_FULL_NAMES.get(abbrev, t.get("teamCommonName", abbrev))
                odds = t.get("playoffOdds", 0)
                record = f"{t['wins']}-{t['losses']}-{t.get('otLosses', 0)}"
                pts = t.get("points", 0)
                cls = odds_class(odds)
                
                status = ""
                if t.get("clinched"):
                    status = '<span class="status-tag tag-clinched">CLINCHED</span>'
                elif t.get("eliminated"):
                    status = '<span class="status-tag tag-eliminated">ELIMINATED</span>'
                
                html += f"""
      <tr>
        <td><a class="team-link" href="/nhl/teams/{abbrev.lower()}">{name}</a></td>
        <td>{record}</td>
        <td>{pts}</td>
        <td class="{cls}">{odds:.1f}%</td>
        <td>{status}</td>
      </tr>"""
            
            html += '\n    </tbody></table>'
        
        # Wildcards
        wc = conf.get("wildcards", [])
        if wc:
            html += '\n    <h3>⭐ Wild Card</h3>'
            html += '\n    <table class="team-table"><thead><tr><th>Team</th><th>Record</th><th>PTS</th><th>Playoff %</th><th>Status</th></tr></thead><tbody>'
            for t in wc:
                abbrev = t["teamAbbrev"]
                name = TEAM_FULL_NAMES.get(abbrev, abbrev)
                odds = t.get("playoffOdds", 0)
                record = f"{t['wins']}-{t['losses']}-{t.get('otLosses', 0)}"
                pts = t.get("points", 0)
                cls = odds_class(odds)
                html += f'\n      <tr><td><a class="team-link" href="/nhl/teams/{abbrev.lower()}">{name}</a></td><td>{record}</td><td>{pts}</td><td class="{cls}">{odds:.1f}%</td><td></td></tr>'
            html += '\n    </tbody></table>'
    
    html += '\n  </div>'
    html += html_footer(gen_at)
    
    out_dir = os.path.join(OUTPUT_DIR, "nhl", "playoff-odds")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(html)


def generate_team_page(team, data):
    """Generate /nhl/teams/{abbrev}/index.html"""
    abbrev = team["teamAbbrev"]
    full_name = TEAM_FULL_NAMES.get(abbrev, team.get("teamName", abbrev))
    odds = team.get("playoffOdds", 0)
    record = f"{team['wins']}-{team['losses']}-{team.get('otLosses', 0)}"
    pts = team.get("points", 0)
    gr = team.get("gamesRemaining", 0)
    gen_at = data["generated_at"]
    
    status = "on the bubble"
    if team.get("clinched"): status = "clinched"
    elif team.get("eliminated"): status = "eliminated"
    
    title = f"{full_name} Playoff Odds — {odds:.1f}% ({status}) | IceChaser"
    desc = f"{full_name} have a {odds:.1f}% chance of making the NHL playoffs. Record: {record}, {pts} points, {gr} games remaining. Updated every 20 minutes from 500,000 Monte Carlo simulations."
    
    html = html_head(title, desc, f"https://icechaser.com/nhl/teams/{abbrev.lower()}", f"{full_name} Playoff Odds — {odds:.1f}%")
    
    # JSON-LD for this team
    html += f"""
  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "SportsTeam",
    "name": "{full_name}",
    "sport": "Ice Hockey",
    "memberOf": {{
      "@type": "SportsOrganization",
      "name": "National Hockey League"
    }}
  }}
  </script>"""
    
    html += f"""
  <div class="static-container">
    <a class="back-link" href="/nhl/playoff-odds">← All Teams</a>
    <div class="static-header" style="text-align:left">
      <h1>{full_name}</h1>
      <p>{team.get('conference', '')} Conference · {team.get('division', '')} Division</p>
    </div>
    
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-value {odds_class(odds)}">{odds:.1f}%</div>
        <div class="stat-label">Playoff Probability</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{record}</div>
        <div class="stat-label">Record (W-L-OT)</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{pts}</div>
        <div class="stat-label">Points</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{gr}</div>
        <div class="stat-label">Games Remaining</div>
      </div>
    </div>"""
    
    # Status
    if team.get("clinched"):
        html += '\n    <div style="padding:12px;background:rgba(63,185,80,0.1);border:1px solid rgba(63,185,80,0.3);border-radius:8px;margin:16px 0;text-align:center"><strong>✓ Clinched Playoff Spot</strong></div>'
    elif team.get("eliminated"):
        html += '\n    <div style="padding:12px;background:rgba(248,81,73,0.1);border:1px solid rgba(248,81,73,0.3);border-radius:8px;margin:16px 0;text-align:center"><strong>✗ Eliminated from Playoff Contention</strong></div>'
    
    # Best/worst case
    best = team.get("best_case_tonight", odds)
    worst = team.get("worst_case_tonight", odds)
    if abs(best - worst) > 0.5:
        html += f"""
    <h2>Tonight's Range</h2>
    <div class="stat-grid">
      <div class="stat-card"><div class="stat-value odds-low">{worst:.1f}%</div><div class="stat-label">Worst Case</div></div>
      <div class="stat-card"><div class="stat-value">{odds:.1f}%</div><div class="stat-label">Current</div></div>
      <div class="stat-card"><div class="stat-value odds-high">{best:.1f}%</div><div class="stat-label">Best Case</div></div>
    </div>"""
    
    # Game scenarios
    scenarios = team.get("game_scenarios", [])
    if scenarios:
        html += '\n    <h2>How Tonight\'s Games Affect the ' + full_name.split()[-1] + '</h2>'
        for sc in scenarios:
            home_pct = sc.get("if_home_reg_win_pct", sc.get("if_home_wins_pct", 0))
            away_pct = sc.get("if_away_reg_win_pct", sc.get("if_away_wins_pct", 0))
            html += f"""
    <div class="scenario-game">
      <strong>{sc.get('away_team', '')} @ {sc.get('home_team', '')}</strong>
      <div style="margin-top:6px">
        If {sc.get('home_team', '')} wins: <span class="{odds_class(home_pct)}">{home_pct:.1f}%</span> · 
        If {sc.get('away_team', '')} wins: <span class="{odds_class(away_pct)}">{away_pct:.1f}%</span>
      </div>
    </div>"""
    
    # What If table
    what_if = team.get("what_if", [])
    if what_if:
        html += '\n    <h2>What If They Finish...</h2>'
        html += '\n    <table class="wif-table"><thead><tr><th>Record</th><th>Final Pts</th><th># Sims</th><th>Make Playoffs</th></tr></thead><tbody>'
        for row in what_if[:15]:
            pct = row.get("playoff_pct", 0)
            cls = odds_class(pct)
            html += f"""
      <tr>
        <td>{row['wins']}-{row['losses']}-{row.get('otl', 0)}</td>
        <td>{row.get('final_points', 0):.0f}</td>
        <td>{row.get('times', 0):,}</td>
        <td class="{cls}">{pct:.1f}%</td>
      </tr>"""
        html += '\n    </tbody></table>'
    
    # Projected standings
    proj_pts = team.get("projected_points")
    proj_seed = team.get("projected_seed")
    proj_record = team.get("projected_record")
    if proj_pts:
        html += f"""
    <h2>🔮 Projected Final Standings</h2>
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin:8px 0">
      <div class="stat-card"><div class="stat-value">{proj_pts:.0f}</div><div class="stat-label">Projected Points</div></div>
      <div class="stat-card"><div class="stat-value" style="color:{'var(--green)' if proj_seed and proj_seed <= 8 else 'var(--red)'};">#{proj_seed or '?'}</div><div class="stat-label">Projected Seed</div></div>
      {f'<div class="stat-card"><div class="stat-value">{proj_record}</div><div class="stat-label">Projected Record</div></div>' if proj_record else ''}
    </div>"""

    # Draft lottery
    draft_pos = team.get("draft_lottery_position")
    draft_pct = team.get("draft_first_pick_pct")
    if draft_pos:
        html += f"""
    <h2>🎰 Draft Lottery</h2>
    <div style="display:flex;gap:12px;margin:8px 0">
      <div class="stat-card"><div class="stat-value">#{draft_pos}</div><div class="stat-label">Lottery Position</div></div>
      <div class="stat-card"><div class="stat-value" style="color:var(--yellow)">{draft_pct}%</div><div class="stat-label">#1 Overall Pick</div></div>
    </div>"""

    # Schedule strength
    avg_elo = team.get("avg_opponent_elo")
    sched_rank = team.get("schedule_difficulty_rank")
    if avg_elo:
        diff = "HARDEST" if sched_rank and sched_rank <= 10 else "EASIEST" if sched_rank and sched_rank >= 23 else "MODERATE"
        html += f"""
    <h2>💪 Remaining Schedule</h2>
    <div style="display:flex;gap:12px;margin:8px 0">
      <div class="stat-card"><div class="stat-value">{avg_elo:.0f}</div><div class="stat-label">Avg Opponent Elo</div></div>
      <div class="stat-card"><div class="stat-value">#{sched_rank}</div><div class="stat-label">{diff}</div></div>
    </div>"""

    # Link to live version
    html += f"""
    <div class="live-cta">
      <a href="/#team-dive">View Live Interactive Dashboard →</a>
      <p style="color:#8b949e;font-size:0.8rem;margin-top:4px">Live scores, real-time scenario updates, and more</p>
    </div>
    
    <h2>All Teams</h2>
    <div class="nav-links">"""
    
    for t in sorted(data["teams"], key=lambda x: x.get("teamAbbrev", "")):
        a = t["teamAbbrev"]
        n = TEAM_FULL_NAMES.get(a, a)
        bold = ' style="font-weight:700;border-color:#58a6ff"' if a == abbrev else ''
        html += f'\n      <a href="/nhl/teams/{a.lower()}"{bold}>{n}</a>'
    
    html += '\n    </div>\n  </div>'
    html += html_footer(gen_at)
    
    out_dir = os.path.join(OUTPUT_DIR, "nhl", "teams", abbrev.lower())
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(html)


def generate_sitemap(data):
    """Generate sitemap.xml"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = [
        "https://icechaser.com/",
        "https://icechaser.com/nhl/playoff-odds",
        "https://icechaser.com/nhl/who-to-root-for",
        f"https://icechaser.com/nhl/games/{today}",
        "https://icechaser.com/nhl/eastern/playoff-odds",
        "https://icechaser.com/nhl/western/playoff-odds",
    ]
    for t in data["teams"]:
        urls.append(f"https://icechaser.com/nhl/teams/{t['teamAbbrev'].lower()}")
    
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for url in urls:
        xml += f'  <url><loc>{url}</loc><lastmod>{now}</lastmod><changefreq>hourly</changefreq></url>\n'
    xml += '</urlset>'
    
    with open(os.path.join(OUTPUT_DIR, "sitemap.xml"), "w") as f:
        f.write(xml)


def generate_daily_games_page(data):
    """Generate /nhl/games/YYYY-MM-DD/index.html"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    games = data.get("todays_games", [])
    gen_at = data["generated_at"]
    
    n_games = len(games)
    live = sum(1 for g in games if g.get("gameState") in ("LIVE", "CRIT"))
    final = sum(1 for g in games if g.get("gameState") in ("FINAL", "OFF"))
    
    title = f"NHL Games {today} — {n_games} Games, Playoff Impact | IceChaser"
    desc = f"All {n_games} NHL games on {today} with real-time playoff implications. See how each game affects every team's playoff odds."
    
    html = html_head(title, desc, f"https://icechaser.com/nhl/games/{today}")
    html += f"""
  <div class="static-container">
    <a class="back-link" href="/nhl/playoff-odds">← Playoff Odds</a>
    <div class="static-header">
      <h1>🏒 NHL Games — {today}</h1>
      <p>{n_games} games · {live} live · {final} final</p>
    </div>
    
    <div class="live-cta">
      <a href="/">View Live Interactive Dashboard →</a>
    </div>"""
    
    if not games:
        html += '\n    <p style="text-align:center;color:#8b949e;padding:40px">No games scheduled today.</p>'
    else:
        for g in games:
            home = g.get("homeTeamAbbrev", "")
            away = g.get("awayTeamAbbrev", "")
            home_name = TEAM_FULL_NAMES.get(home, home)
            away_name = TEAM_FULL_NAMES.get(away, away)
            state = g.get("gameState", "FUT")
            home_odds = g.get("homePlayoffOdds", 50)
            away_odds = g.get("awayPlayoffOdds", 50)
            impact = g.get("playoffImpactLabel", "NONE")
            
            state_text = "🔴 LIVE" if state in ("LIVE", "CRIT") else "FINAL" if state in ("FINAL", "OFF") else "Upcoming"
            score_text = ""
            if state in ("FINAL", "OFF", "LIVE", "CRIT"):
                score_text = f" — {g.get('awayScore', 0)}-{g.get('homeScore', 0)}"
            
            html += f"""
    <div class="scenario-game">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <strong>
          <a class="team-link" href="/nhl/teams/{away.lower()}">{away_name}</a> @
          <a class="team-link" href="/nhl/teams/{home.lower()}">{home_name}</a>
        </strong>
        <span style="color:#8b949e">{state_text}{score_text}</span>
      </div>
      <div style="margin-top:8px;display:flex;gap:20px;font-size:0.9rem">
        <span>{away}: <span class="{odds_class(away_odds)}">{away_odds:.1f}%</span> playoff odds</span>
        <span>{home}: <span class="{odds_class(home_odds)}">{home_odds:.1f}%</span> playoff odds</span>
        <span style="color:#8b949e">Impact: {impact}</span>
      </div>
    </div>"""
    
    html += '\n  </div>'
    html += html_footer(gen_at)
    
    out_dir = os.path.join(OUTPUT_DIR, "nhl", "games", today)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(html)


def generate_conference_pages(data):
    """Generate /nhl/{conference}/playoff-odds/index.html"""
    gen_at = data["generated_at"]
    
    for conf_name in ["Eastern", "Western"]:
        conf = data.get("conferences", {}).get(conf_name, {})
        conf_teams = [t for t in data["teams"] if t.get("conference") == conf_name]
        conf_teams.sort(key=lambda t: -t.get("playoffOdds", 0))
        
        clinched = sum(1 for t in conf_teams if t.get("clinched"))
        bubble = sum(1 for t in conf_teams if 20 < t.get("playoffOdds", 0) < 80)
        slug = conf_name.lower()
        
        title = f"NHL {conf_name} Conference Playoff Odds — {clinched} Clinched | IceChaser"
        desc = f"{conf_name} Conference playoff odds from 500,000 simulations. {clinched} clinched, {bubble} on the bubble."
        
        html = html_head(title, desc, f"https://icechaser.com/nhl/{slug}/playoff-odds")
        html += f"""
  <div class="static-container">
    <a class="back-link" href="/nhl/playoff-odds">← All Teams</a>
    <div class="static-header">
      <h1>{"🔵" if conf_name == "Eastern" else "🟣"} {conf_name} Conference Playoff Odds</h1>
      <p>{len(conf_teams)} teams · {clinched} clinched · {bubble} on the bubble</p>
    </div>"""
        
        for div_name, div_teams in conf.get("divisions", {}).items():
            html += f'\n    <h2>{div_name} Division</h2>'
            html += '\n    <table class="team-table"><thead><tr><th>Team</th><th>Record</th><th>PTS</th><th>Playoff %</th><th>Status</th></tr></thead><tbody>'
            for t in div_teams:
                abbrev = t["teamAbbrev"]
                name = TEAM_FULL_NAMES.get(abbrev, abbrev)
                odds = t.get("playoffOdds", 0)
                record = f"{t['wins']}-{t['losses']}-{t.get('otLosses', 0)}"
                pts = t.get("points", 0)
                status = ""
                if t.get("clinched"): status = '<span class="status-tag tag-clinched">CLINCHED</span>'
                elif t.get("eliminated"): status = '<span class="status-tag tag-eliminated">ELIMINATED</span>'
                html += f'\n      <tr><td><a class="team-link" href="/nhl/teams/{abbrev.lower()}">{name}</a></td><td>{record}</td><td>{pts}</td><td class="{odds_class(odds)}">{odds:.1f}%</td><td>{status}</td></tr>'
            html += '\n    </tbody></table>'
        
        wc = conf.get("wildcards", [])
        if wc:
            html += '\n    <h3>⭐ Wild Card</h3>'
            html += '\n    <table class="team-table"><thead><tr><th>Team</th><th>Record</th><th>PTS</th><th>Playoff %</th></tr></thead><tbody>'
            for t in wc:
                abbrev = t["teamAbbrev"]
                name = TEAM_FULL_NAMES.get(abbrev, abbrev)
                odds = t.get("playoffOdds", 0)
                record = f"{t['wins']}-{t['losses']}-{t.get('otLosses', 0)}"
                html += f'\n      <tr><td><a class="team-link" href="/nhl/teams/{abbrev.lower()}">{name}</a></td><td>{record}</td><td>{t.get("points",0)}</td><td class="{odds_class(odds)}">{odds:.1f}%</td></tr>'
            html += '\n    </tbody></table>'
        
        html += '\n  </div>'
        html += html_footer(gen_at)
        
        out_dir = os.path.join(OUTPUT_DIR, "nhl", slug, "playoff-odds")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "index.html"), "w") as f:
            f.write(html)


def generate_who_to_root_for(data):
    """Generate /nhl/who-to-root-for/index.html — the killer SEO page"""
    games = data.get("todays_games", [])
    teams_map = {t["teamAbbrev"]: t for t in data["teams"]}
    gen_at = data["generated_at"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    title = f"Who to Root For Tonight — NHL Playoff Implications {today} | IceChaser"
    desc = f"Every NHL game tonight ranked by playoff impact. See which games matter most and who you should root for based on 500,000 Monte Carlo simulations."
    
    html = html_head(title, desc, "https://icechaser.com/nhl/who-to-root-for")
    html += """
  <div class="static-container">
    <a class="back-link" href="/nhl/playoff-odds">← Playoff Odds</a>
    <div class="static-header">
      <h1>🏒 Who to Root For Tonight</h1>
      <p>Tonight's games ranked by playoff impact</p>
    </div>
    
    <div class="live-cta">
      <a href="/">View Live Interactive Dashboard →</a>
    </div>"""
    
    if not games:
        html += '\n    <p style="text-align:center;color:#8b949e;padding:40px">No games tonight. Check back tomorrow!</p>'
    else:
        # Sort by combined playoff relevance (bubble teams in the game)
        def game_importance(g):
            home = teams_map.get(g.get("homeTeamAbbrev"), {})
            away = teams_map.get(g.get("awayTeamAbbrev"), {})
            h_odds = home.get("playoffOdds", 50)
            a_odds = away.get("playoffOdds", 50)
            # Games between two bubble teams are most important
            h_bubble = 50 - abs(h_odds - 50)  # max at 50%
            a_bubble = 50 - abs(a_odds - 50)
            return h_bubble + a_bubble
        
        sorted_games = sorted(games, key=game_importance, reverse=True)
        
        for i, g in enumerate(sorted_games):
            home_abbrev = g.get("homeTeamAbbrev", "")
            away_abbrev = g.get("awayTeamAbbrev", "")
            home = teams_map.get(home_abbrev, {})
            away = teams_map.get(away_abbrev, {})
            home_name = TEAM_FULL_NAMES.get(home_abbrev, home_abbrev)
            away_name = TEAM_FULL_NAMES.get(away_abbrev, away_abbrev)
            home_odds = home.get("playoffOdds", 50)
            away_odds = away.get("playoffOdds", 50)
            
            state = g.get("gameState", "FUT")
            state_text = "🔴 LIVE" if state in ("LIVE", "CRIT") else "FINAL" if state in ("FINAL", "OFF") else ""
            
            impact = g.get("playoffImpactLabel", "LOW")
            impact_emoji = "🔥" if impact == "CRITICAL" else "⚡" if impact == "HIGH" else "📊" if impact == "MEDIUM" else "📌"
            
            # Who should a neutral fan root for?
            # Root for the underdog (lower playoff odds) for maximum chaos
            if home_odds < away_odds:
                root_for = home_name
                root_why = f"They're the underdog at {home_odds:.1f}% — a win shakes up the race"
            elif away_odds < home_odds:
                root_for = away_name
                root_why = f"They're the underdog at {away_odds:.1f}% — a win shakes up the race"
            else:
                root_for = "Coin flip"
                root_why = "Both teams at similar odds"
            
            # If both clinched or eliminated, low stakes
            if (home.get("clinched") and away.get("clinched")) or (home.get("eliminated") and away.get("eliminated")):
                root_for = "Enjoy the game"
                root_why = "No playoff implications"
            
            html += f"""
    <div class="scenario-game" style="border-left:3px solid {'#f85149' if impact in ('CRITICAL','HIGH') else '#d2992a' if impact == 'MEDIUM' else '#30363d'}">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <span style="font-size:0.8rem;color:#8b949e">#{i+1}</span>
          <strong style="margin-left:6px">
            <a class="team-link" href="/nhl/teams/{away_abbrev.lower()}">{away_name}</a>
            <span style="color:#8b949e">@</span>
            <a class="team-link" href="/nhl/teams/{home_abbrev.lower()}">{home_name}</a>
          </strong>
          {f'<span style="margin-left:8px">{state_text}</span>' if state_text else ''}
        </div>
        <span>{impact_emoji} {impact}</span>
      </div>
      <div style="margin-top:8px;display:flex;gap:20px;font-size:0.9rem">
        <span>{away_abbrev}: <span class="{odds_class(away_odds)}">{away_odds:.1f}%</span></span>
        <span>{home_abbrev}: <span class="{odds_class(home_odds)}">{home_odds:.1f}%</span></span>
      </div>
      <div style="margin-top:6px;font-size:0.9rem">
        <strong>Root for:</strong> {root_for} — <em>{root_why}</em>
      </div>
    </div>"""
    
    html += '\n  </div>'
    html += html_footer(gen_at)
    
    out_dir = os.path.join(OUTPUT_DIR, "nhl", "who-to-root-for")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(html)


def generate_rss_feed(data):
    """Generate /rss.xml — RSS feed of daily playoff odds updates"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    gen_at = data["generated_at"]
    
    bubble = [t for t in data["teams"] if 5 < t.get("playoffOdds", 0) < 95 and not t.get("clinched") and not t.get("eliminated")]
    bubble.sort(key=lambda t: -t.get("playoffOdds", 0))
    
    clinched = sum(1 for t in data["teams"] if t.get("clinched"))
    eliminated = sum(1 for t in data["teams"] if t.get("eliminated"))
    
    desc_lines = [f"NHL Playoff Odds Update — {today}"]
    desc_lines.append(f"{clinched} clinched, {eliminated} eliminated.")
    for t in bubble[:10]:
        name = TEAM_FULL_NAMES.get(t["teamAbbrev"], t["teamAbbrev"])
        desc_lines.append(f"{name}: {t['playoffOdds']:.1f}%")
    
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>IceChaser — NHL Playoff Odds</title>
  <link>https://icechaser.com</link>
  <description>Daily NHL playoff probability updates from 500,000 Monte Carlo simulations</description>
  <language>en-us</language>
  <lastBuildDate>{gen_at}</lastBuildDate>
  <atom:link href="https://icechaser.com/rss.xml" rel="self" type="application/rss+xml" />
  <item>
    <title>NHL Playoff Odds — {today}</title>
    <link>https://icechaser.com/nhl/games/{today}</link>
    <description>{chr(10).join(desc_lines)}</description>
    <pubDate>{gen_at}</pubDate>
    <guid>https://icechaser.com/nhl/games/{today}</guid>
  </item>
</channel>
</rss>"""
    
    with open(os.path.join(OUTPUT_DIR, "rss.xml"), "w") as f:
        f.write(xml)


def main():
    print("📄 Generating static pages...")
    data = load_data()
    
    generate_main_page(data)
    print(f"   ✓ /nhl/playoff-odds/")
    
    for team in data["teams"]:
        generate_team_page(team, data)
    print(f"   ✓ {len(data['teams'])} team pages")
    
    generate_daily_games_page(data)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"   ✓ /nhl/games/{today}/")
    
    generate_conference_pages(data)
    print(f"   ✓ /nhl/eastern/ + /nhl/western/")
    
    generate_who_to_root_for(data)
    print(f"   ✓ /nhl/who-to-root-for/")
    
    generate_rss_feed(data)
    print(f"   ✓ /rss.xml")
    
    generate_sitemap(data)
    print(f"   ✓ sitemap.xml")
    
    print("✅ Static pages generated")


if __name__ == "__main__":
    main()
