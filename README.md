# 🏒 IceChaser — NHL Playoff Odds Tracker

A public-facing website that shows NHL playoff odds with narrative storytelling. Powered by Monte Carlo simulation (10,000 runs per update).

## Structure

```
icechaser/
├── backend/
│   ├── nhl_api.py        # NHL API client
│   ├── simulator.py      # Monte Carlo simulator
│   ├── narrative.py      # Narrative text generator
│   └── generate_data.py  # Main script
├── frontend/
│   ├── index.html        # Main page
│   ├── style.css         # Dark modern styles
│   └── app.js            # Frontend logic
└── data/
    └── playoff_odds.json  # Generated odds data
```

## Quick Start

### 1. Generate data
```bash
cd backend
python3 generate_data.py
```

### 2. Serve the frontend
```bash
cd frontend
python3 -m http.server 8080
# Open http://localhost:8080
```

Or serve with nginx/caddy pointing to the `frontend/` directory, with `/data/` symlinked or accessible.

### 3. Auto-update (cron)
Add to crontab to refresh every 15 minutes during the season:
```
*/15 * * * * cd /path/to/icechaser/backend && python3 generate_data.py
```

## How It Works

1. **NHL API** — Fetches live standings and today's schedule from `api-web.nhle.com`
2. **Monte Carlo Simulator** — Runs 10,000 simulations of the remaining season, awarding home teams ~54% win probability adjusted by points pace
3. **Narratives** — Generates human-readable text about the playoff race
4. **Frontend** — Reads `data/playoff_odds.json`, renders animated odds bars, detects changes via localStorage

## Playoff Format
- Top 3 teams per division qualify
- Top 2 remaining teams per conference (wildcards) qualify
- 8 teams per conference = 16 total playoff spots

## Requirements
- Python 3.8+
- `requests` library (`pip install requests`)
