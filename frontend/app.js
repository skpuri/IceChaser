/**
 * IceChaser - Frontend App
 * Fetches playoff_odds.json and renders the full UI.
 */

const DATA_URL = '../data/playoff_odds.json';
const REFRESH_INTERVAL = 5 * 60 * 1000; // 5 minutes
const STORAGE_KEY = 'icechaser_prev_odds';
const DIVE_TEAM_KEY = 'icechaser_dive_team';
const NHL_LOGO_BASE = 'https://assets.nhle.com/logos/nhl/svg/';

let refreshTimer = null;
let currentData = null;

// ─── Bootstrap ───────────────────────────────────────────────────────────────

async function init() {
  await loadData();
  scheduleRefresh();
}

function scheduleRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(loadData, REFRESH_INTERVAL);
}

async function forceRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  await loadData();
  scheduleRefresh();
}

async function loadData() {
  try {
    const resp = await fetch(`${DATA_URL}?_=${Date.now()}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const prev = getPrevOdds();
    render(data, prev);
    savePrevOdds(data);
    currentData = data;
  } catch (err) {
    renderError(err.message);
  }
}

// ─── LocalStorage ─────────────────────────────────────────────────────────────

function getPrevOdds() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

function savePrevOdds(data) {
  try {
    const odds = {};
    (data.teams || []).forEach(t => {
      odds[t.teamAbbrev] = t.playoffOdds;
    });
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      timestamp: data.generated_at,
      odds
    }));
  } catch {}
}

// ─── Main Render ──────────────────────────────────────────────────────────────

function render(data, prev) {
  const app = document.getElementById('app');
  const prevOdds = prev?.odds || null;

  // Update header timestamp
  const tsEl = document.getElementById('last-updated');
  if (tsEl) {
    const dt = new Date(data.generated_at);
    tsEl.textContent = `Updated ${formatTime(dt)}`;
  }

  app.innerHTML = '';
  app.className = '';

  const container = el('div', { class: 'container' });

  container.appendChild(renderHero(data.narratives));

  // Wrap game cards in dark strip for light mode
  const gamesStrip = el('div', { class: 'games-dark-strip' });
  gamesStrip.appendChild(renderGames(data.todays_games || []));
  if (data.tomorrows_games && data.tomorrows_games.length) {
    gamesStrip.appendChild(renderTomorrowGames(data.tomorrows_games));
  }
  container.appendChild(gamesStrip);

  container.appendChild(renderStandingsTable(data.conferences || {}, data.teams || [], data.todays_games || []));
  container.appendChild(renderTeamDeepDive(data.teams || [], data.tomorrows_games || []));
  container.appendChild(renderBubbleWatch(data.teams || [], prevOdds));

  app.appendChild(container);
}

function renderError(msg) {
  const app = document.getElementById('app');
  app.innerHTML = `
    <div class="container" style="padding:40px 0">
      <div class="error-card">
        <div style="font-size:32px;margin-bottom:12px">⚠️</div>
        <div style="font-size:16px;font-weight:700;margin-bottom:8px">Failed to load playoff data</div>
        <div style="color:#8b949e;font-size:13px">${msg}</div>
        <div style="margin-top:16px">
          <button class="refresh-btn" onclick="forceRefresh()">Try Again</button>
        </div>
      </div>
    </div>`;
}

// ─── Hero Section ─────────────────────────────────────────────────────────────

function renderHero(narratives) {
  if (!narratives) return el('div');

  const section = el('section', { class: 'section hero' });

  const headline = el('div', { class: 'hero-headline fade-in' });
  headline.innerHTML = `<h1>${esc(narratives.headline || 'Welcome to IceChaser — NHL Playoff Odds')}</h1>`;
  section.appendChild(headline);

  const cards = el('div', { class: 'hero-narratives' });
  const narrativeItems = [
    { key: 'tonight_stakes', label: "Tonight's Stakes", accent: 'accent-stakes', icon: '🔥' },
    { key: 'bubble_watch', label: 'Bubble Watch', accent: 'accent-bubble', icon: '🫧' },
    { key: 'biggest_movers', label: 'Movers', accent: 'accent-movers', icon: '📈' },
  ];

  narrativeItems.forEach(item => {
    const text = narratives[item.key];
    if (!text) return;
    const card = el('div', { class: `narrative-card fade-in ${item.accent}` });
    card.innerHTML = `<div class="label">${item.icon} ${item.label}</div><div>${esc(text)}</div>`;
    cards.appendChild(card);
  });

  section.appendChild(cards);
  return section;
}

// ─── Tonight's Games ──────────────────────────────────────────────────────────

function renderGames(games) {
  const section = el('section', { class: 'section' });
  const allFinal = games.length > 0 && games.every(g => g.gameState === 'FINAL' || g.gameState === 'OFF');
  const title = allFinal ? "Tonight's Results" : "Tonight's Games";
  const icon = allFinal ? '✅' : '🎮';
  section.appendChild(renderSectionTitle(icon, title, games.length ? `${games.length} game${games.length !== 1 ? 's' : ''}` : null));

  if (!games.length) {
    const empty = el('div', { class: 'no-games' });
    empty.innerHTML = '<div class="big">🌙</div><div>No games scheduled today</div>';
    section.appendChild(empty);
    return section;
  }

  const grid = el('div', { class: 'games-grid' });

  games.forEach(game => {
    const card = el('div', { class: `game-card impact-${game.playoffImpactLabel || 'NONE'} fade-in` });

    const homeOdds = game.homePlayoffOdds ?? 50;
    const awayOdds = game.awayPlayoffOdds ?? 50;
    const isFinal = (game.gameState === 'FINAL' || game.gameState === 'OFF');
    const isLive = (game.gameState === 'LIVE' || game.gameState === 'CRIT');
    const homeScore = game.homeScore ?? 0;
    const awayScore = game.awayScore ?? 0;
    const homeWon = isFinal && homeScore > awayScore;
    const awayWon = isFinal && awayScore > homeScore;

    // Score display
    let scoreHtml = '';
    if (isFinal) {
      scoreHtml = `<div class="game-score final">
        <span class="score ${awayWon ? 'winner' : 'loser'}">${awayScore}</span>
        <span class="score-dash">-</span>
        <span class="score ${homeWon ? 'winner' : 'loser'}">${homeScore}</span>
        <span class="score-label">FINAL</span>
      </div>`;
    } else if (isLive) {
      const clock = game.timeRemaining || '';
      const period = game.period || '';
      const isInt = game.inIntermission;
      const periodLabel = period ? (isInt ? ` ${period} INT` : ` ${period}P`) : '';
      const liveLabel = clock ? `${clock}${periodLabel}` : 'LIVE';
      scoreHtml = `<div class="game-score live">
        <span class="score">${awayScore}</span>
        <span class="score-dash">-</span>
        <span class="score">${homeScore}</span>
        <span class="score-label live-pulse">${liveLabel}</span>
      </div>`;
    }

    card.innerHTML = `
      <div class="game-matchup">
        <div class="game-team ${awayWon ? 'team-won' : ''} ${isFinal && !awayWon ? 'team-lost' : ''} clickable" onclick="selectTeamDive('${game.awayTeamAbbrev}')">
          <img class="team-logo" src="${logoUrlDark(game.awayTeamAbbrev)}" alt="${game.awayTeamAbbrev}" onerror="this.style.display='none'">
          <div class="team-abbrev">${game.awayTeamAbbrev}</div>
          <div class="team-odds-pct ${oddsClass(awayOdds)}" style="${oddsStyle(awayOdds)}">${awayOdds.toFixed(0)}%</div>
        </div>
        ${scoreHtml || '<div class="game-vs">@</div>'}
        <div class="game-team ${homeWon ? 'team-won' : ''} ${isFinal && !homeWon ? 'team-lost' : ''} clickable" onclick="selectTeamDive('${game.homeTeamAbbrev}')">
          <img class="team-logo" src="${logoUrlDark(game.homeTeamAbbrev)}" alt="${game.homeTeamAbbrev}" onerror="this.style.display='none'">
          <div class="team-abbrev">${game.homeTeamAbbrev}</div>
          <div class="team-odds-pct ${oddsClass(homeOdds)}" style="${oddsStyle(homeOdds)}">${homeOdds.toFixed(0)}%</div>
        </div>
      </div>
      <div class="game-meta">
        <span>${isFinal ? 'Final' : formatGameTime(game.gameTime)}</span>
        <span class="impact-badge ${game.playoffImpactLabel || 'NONE'}">${impactLabel(game)}</span>
      </div>`;

    grid.appendChild(card);
  });

  section.appendChild(grid);
  return section;
}

function renderTomorrowGames(games) {
  const section = el('section', { class: 'section' });
  section.appendChild(renderSectionTitle('📅', "Tomorrow's Games", `${games.length} game${games.length !== 1 ? 's' : ''}`));

  const grid = el('div', { class: 'games-grid' });

  games.forEach(game => {
    const card = el('div', { class: `game-card impact-${game.playoffImpactLabel || 'NONE'} fade-in tomorrow-card` });

    const homeOdds = game.homePlayoffOdds ?? 50;
    const awayOdds = game.awayPlayoffOdds ?? 50;

    card.innerHTML = `
      <div class="game-matchup">
        <div class="game-team clickable" onclick="selectTeamDive('${game.awayTeamAbbrev}')">
          <img class="team-logo" src="${logoUrlDark(game.awayTeamAbbrev)}" alt="${game.awayTeamAbbrev}" onerror="this.style.display='none'">
          <div class="team-abbrev">${game.awayTeamAbbrev}</div>
          <div class="team-odds-pct ${oddsClass(awayOdds)}" style="${oddsStyle(awayOdds)}">${awayOdds.toFixed(0)}%</div>
        </div>
        <div class="game-vs">@</div>
        <div class="game-team clickable" onclick="selectTeamDive('${game.homeTeamAbbrev}')">
          <img class="team-logo" src="${logoUrlDark(game.homeTeamAbbrev)}" alt="${game.homeTeamAbbrev}" onerror="this.style.display='none'">
          <div class="team-abbrev">${game.homeTeamAbbrev}</div>
          <div class="team-odds-pct ${oddsClass(homeOdds)}" style="${oddsStyle(homeOdds)}">${homeOdds.toFixed(0)}%</div>
        </div>
      </div>
      <div class="game-meta">
        <span>${formatGameTime(game.gameTime)}</span>
        <span class="impact-badge ${game.playoffImpactLabel || 'NONE'}">${impactLabel(game)}</span>
      </div>`;

    grid.appendChild(card);
  });

  section.appendChild(grid);
  return section;
}

function impactLabel(game) {
  const label = game.playoffImpactLabel || 'NONE';
  if (label === 'CRITICAL') return '🔥 CRITICAL';
  if (label === 'HIGH') return '⚡ HIGH STAKES';
  if (label === 'MEDIUM') return '📊 PLAYOFF RACE';
  if (label === 'LOW') return '🏒 LOW IMPACT';
  return '🏒 NHL GAME';
}

// ─── Playoff Race ─────────────────────────────────────────────────────────────

function renderPlayoffRace(conferences) {
  const section = el('section', { class: 'section' });
  section.appendChild(renderSectionTitle('🏆', 'Playoff Race'));

  const grid = el('div', { class: 'conferences-grid' });

  ['Eastern', 'Western'].forEach(confName => {
    const conf = conferences[confName];
    if (!conf) return;

    const panel = el('div', { class: 'conference-panel fade-in' });
    const header = el('div', { class: 'conference-header' });
    header.innerHTML = `<span>${confName === 'Eastern' ? '🔵' : '🟣'}</span> ${confName} Conference`;
    panel.appendChild(header);

    // Divisions
    const divisions = conf.divisions || {};
    Object.entries(divisions).forEach(([divName, divTeams]) => {
      const divHeader = el('div', { class: 'division-header' });
      divHeader.textContent = divName + ' Division';
      panel.appendChild(divHeader);

      (divTeams || []).forEach((team, idx) => {
        panel.appendChild(renderTeamRow(team, idx + 1, null));
      });
    });

    // Wildcards
    const wildcards = conf.wildcards || [];
    if (wildcards.length) {
      const wcHeader = el('div', { class: 'wildcard-header' });
      wcHeader.innerHTML = '⭐ Wild Card';
      panel.appendChild(wcHeader);
      wildcards.forEach((team, idx) => {
        panel.appendChild(renderTeamRow(team, idx + 1, null));
      });
    }

    grid.appendChild(panel);
  });

  section.appendChild(grid);
  return section;
}

function renderTeamRow(team, rank, prevOdds) {
  const abbrev = team.teamAbbrev;
  const odds = team.playoffOdds ?? 0;
  const prevTeamOdds = prevOdds ? (prevOdds[abbrev] ?? null) : null;
  const delta = prevTeamOdds !== null ? odds - prevTeamOdds : null;

  const row = el('div', { class: 'team-row' });

  let statusHtml = '';
  if (team.clinched) {
    statusHtml = '<span class="status-badge clinched">✓ In</span>';
  } else if (team.eliminated) {
    statusHtml = '<span class="status-badge eliminated">✗ Out</span>';
  }

  let deltaHtml = '';
  if (delta !== null && Math.abs(delta) >= 0.5) {
    const cls = delta > 0 ? 'delta-up' : 'delta-down';
    const arrow = delta > 0 ? '▲' : '▼';
    deltaHtml = `<span class="delta-arrow ${cls}">${arrow}${Math.abs(delta).toFixed(0)}%</span>`;
  }

  const oddsColor = oddsClass(odds);
  const barClass = oddsBarClass(odds);
  const record = `${team.wins}-${team.losses}-${team.otLosses}`;

  row.innerHTML = `
    <div class="rank-num">${rank}</div>
    <img class="team-row-logo" src="${logoUrl(abbrev)}" alt="${abbrev}" onerror="this.style.display='none'">
    <div class="team-row-info">
      <div class="team-row-name">${esc(team.teamCommonName || team.teamName || abbrev)}</div>
      <div class="team-row-record">${record} · ${team.points} pts · ${team.gamesRemaining} left</div>
    </div>
    <div class="team-row-odds">
      <div class="odds-pct ${oddsColor}">${odds.toFixed(0)}% ${deltaHtml}</div>
      <div class="odds-bar-wrap">
        <div class="odds-bar ${barClass}" style="width:${Math.min(100, odds)}%"></div>
      </div>
      ${statusHtml}
    </div>`;

  return row;
}

// ─── Bubble Watch ─────────────────────────────────────────────────────────────

function renderBubbleWatch(teams, prevOdds) {
  const bubbleTeams = teams
    .filter(t => t.playoffOdds >= 15 && t.playoffOdds <= 85 && !t.clinched && !t.eliminated)
    .sort((a, b) => Math.abs(b.playoffOdds - 50) - Math.abs(a.playoffOdds - 50));

  const section = el('section', { class: 'section' });
  section.appendChild(renderSectionTitle('🫧', 'Bubble Watch', bubbleTeams.length ? `${bubbleTeams.length} teams` : null));

  if (!bubbleTeams.length) {
    const empty = el('div', { class: 'no-games' });
    empty.innerHTML = '<div class="big">✅</div><div>The playoff picture is nearly decided — most spots are locked in.</div>';
    section.appendChild(empty);
    return section;
  }

  const grid = el('div', { class: 'bubble-grid' });

  bubbleTeams.slice(0, 12).forEach(team => {
    const odds = team.playoffOdds ?? 0;
    const abbrev = team.teamAbbrev;
    const prevTeamOdds = prevOdds ? (prevOdds[abbrev] ?? null) : null;
    const delta = prevTeamOdds !== null ? odds - prevTeamOdds : null;

    const card = el('div', { class: 'bubble-card fade-in' });
    const record = `${team.wins}-${team.losses}-${team.otLosses}`;

    let deltaHtml = '';
    if (delta !== null && Math.abs(delta) >= 0.5) {
      const cls = delta > 0 ? 'delta-up' : 'delta-down';
      const arrow = delta > 0 ? '▲' : '▼';
      deltaHtml = `<span class="delta-arrow ${cls}" style="font-size:13px">${arrow}${Math.abs(delta).toFixed(0)}%</span>`;
    }

    const oddsColor = oddsClass(odds);
    const barClass = oddsBarClass(odds);

    card.innerHTML = `
      <div class="bubble-team-header">
        <img class="bubble-logo" src="${logoUrl(abbrev)}" alt="${abbrev}" onerror="this.style.display='none'">
        <div>
          <div class="bubble-team-name">${esc(team.teamCommonName || abbrev)}</div>
          <div class="bubble-record">${record} · ${team.points} pts</div>
        </div>
      </div>
      <div>
        <div class="bubble-odds-big ${oddsColor}">${odds.toFixed(0)}% ${deltaHtml}</div>
        <div class="bubble-label">playoff probability</div>
      </div>
      <div class="bubble-progress">
        <div class="bubble-bar ${barClass}" style="width:${Math.min(100, odds)}%"></div>
      </div>
      <div class="bubble-games-rem">${team.gamesRemaining} games remaining</div>`;

    grid.appendChild(card);
  });

  section.appendChild(grid);
  return section;
}

// ─── Team Deep Dive ───────────────────────────────────────────────────────────

function renderTeamDeepDive(teams, tomorrowGames) {
  const section = el('section', { id: 'team-dive', class: 'section' });
  section.appendChild(renderSectionTitle('🔍', 'Team Deep Dive'));

  const selectWrap = el('div', { class: 'team-select-wrap' });
  const select = el('select', { id: 'team-select' });

  const defaultOpt = el('option');
  defaultOpt.value = '';
  defaultOpt.textContent = 'Select a team...';
  select.appendChild(defaultOpt);

  // Sort teams alphabetically by common name
  const sorted = [...teams].sort((a, b) => {
    const nameA = (a.teamCommonName || a.teamName || a.teamAbbrev).toLowerCase();
    const nameB = (b.teamCommonName || b.teamName || b.teamAbbrev).toLowerCase();
    return nameA.localeCompare(nameB);
  });

  sorted.forEach(team => {
    const opt = el('option');
    opt.value = team.teamAbbrev;
    opt.textContent = `${team.teamCommonName || team.teamName || team.teamAbbrev} (${team.teamAbbrev})`;
    select.appendChild(opt);
  });

  selectWrap.appendChild(select);
  section.appendChild(selectWrap);

  const diveContent = el('div', { id: 'dive-content', class: 'hidden' });
  section.appendChild(diveContent);

  // Restore persisted selection
  const savedTeam = localStorage.getItem(DIVE_TEAM_KEY);
  if (savedTeam) {
    select.value = savedTeam;
  }

  // Wire up event
  select.addEventListener('change', () => {
    const abbrev = select.value;
    if (!abbrev) {
      diveContent.classList.add('hidden');
      diveContent.innerHTML = '';
      localStorage.removeItem(DIVE_TEAM_KEY);
      return;
    }
    localStorage.setItem(DIVE_TEAM_KEY, abbrev);
    const team = teams.find(t => t.teamAbbrev === abbrev);
    if (team) {
      renderDivePanel(team, teams, diveContent, tomorrowGames);
      diveContent.classList.remove('hidden');
    }
  });

  // Auto-render if there's a saved team
  if (savedTeam) {
    const team = teams.find(t => t.teamAbbrev === savedTeam);
    if (team) {
      renderDivePanel(team, teams, diveContent);
      diveContent.classList.remove('hidden');
    }
  }

  return section;
}

function renderDivePanel(team, allTeams, container, tomorrowGames) {
  container.innerHTML = '';

  const abbrev = team.teamAbbrev;
  const odds = team.playoffOdds ?? 0;
  const record = `${team.wins}-${team.losses}-${team.otLosses}`;
  const oddsColor = oddsClass(odds);

  // ── Header card ──
  const header = el('div', { class: 'dive-header fade-in' });

  let statusBadge = '';
  if (team.clinched) {
    statusBadge = '<div class="dive-status-badge clinched">✓ Clinched Playoff Spot</div>';
  } else if (team.eliminated) {
    statusBadge = '<div class="dive-status-badge eliminated">✗ Eliminated</div>';
  }

  const divPos = team.divisionRank ? `${team.division} Division · Rank #${team.divisionRank}` :
    (team.division ? `${team.division} Division` : '');

  header.innerHTML = `
    <img class="dive-logo" src="${logoUrl(abbrev)}" alt="${abbrev}" onerror="this.style.display='none'">
    <div class="dive-team-info">
      <div class="dive-team-name">${esc(team.teamName || abbrev)}</div>
      <div class="dive-record">${record} · ${team.points} pts · ${team.gamesRemaining} games left</div>
      ${divPos ? `<div class="dive-division-pos">${esc(divPos)}</div>` : ''}
    </div>
    <div class="dive-odds-block">
      <div class="dive-odds-big ${oddsColor}">${odds.toFixed(0)}%</div>
      <div class="dive-odds-label">Playoff Probability</div>
      ${statusBadge}
    </div>`;
  container.appendChild(header);

  // ── Seed Probability Breakdown ──
  const seedProbs = team.seed_probs || {};
  if (Object.keys(seedProbs).length) {
    const seedSection = el('div', { class: 'dive-seed-chart fade-in' });
    const seedTitle = el('div', { class: 'dive-section-label' });
    seedTitle.textContent = 'SEEDING BREAKDOWN';
    seedSection.appendChild(seedTitle);

    const chart = el('div', { class: 'seed-bar-chart' });

    // Playoff seeds 1-8
    const playoffHeader = el('div', { class: 'seed-group-label playoff-label' });
    playoffHeader.textContent = 'PLAYOFF SEEDS';
    chart.appendChild(playoffHeader);

    const playoffSum = [1,2,3,4,5,6,7,8].reduce((s, seed) => s + (seedProbs[seed] || 0), 0);
    for (let seed = 1; seed <= 8; seed++) {
      const pct = seedProbs[seed] || 0;
      const row = el('div', { class: 'seed-row' });
      row.innerHTML = `
        <div class="seed-label">#${seed} seed</div>
        <div class="seed-track"><div class="seed-fill playoff-fill" style="width:${pct}%"></div></div>
        <div class="seed-pct">${pct > 0 ? pct.toFixed(1) + '%' : '—'}</div>`;
      chart.appendChild(row);
    }

    // Divider
    const divider = el('div', { class: 'seed-divider' });
    chart.appendChild(divider);

    // Miss seeds 9-16
    const missHeader = el('div', { class: 'seed-group-label miss-label' });
    missHeader.textContent = 'MISS PLAYOFFS';
    chart.appendChild(missHeader);

    const missSum = [9,10,11,12,13,14,15,16].reduce((s, seed) => s + (seedProbs[seed] || 0), 0);
    for (let seed = 9; seed <= 16; seed++) {
      const pct = seedProbs[seed] || 0;
      const row = el('div', { class: 'seed-row' });
      row.innerHTML = `
        <div class="seed-label">#${seed}</div>
        <div class="seed-track"><div class="seed-fill miss-fill" style="width:${pct}%"></div></div>
        <div class="seed-pct">${pct > 0 ? pct.toFixed(1) + '%' : '—'}</div>`;
      chart.appendChild(row);
    }

    // Tragic number note
    if (missSum > 0) {
      const tragic = el('div', { class: 'seed-tragic' });
      tragic.textContent = `Tragic number: ${(100 - (seedProbs[8] || 0) - (seedProbs[9] || 0)).toFixed(0)} pts needed to pass ${missSum.toFixed(0)}% of miss scenarios`;
      chart.appendChild(tragic);
    }

    seedSection.appendChild(chart);
    container.appendChild(seedSection);
  }

  // ── Tonight's Range (only show if there's actual variation) ──
  const bestCase = team.best_case_tonight ?? odds;
  const mediumCase = team.medium_case_tonight ?? odds;
  const worstCase = team.worst_case_tonight ?? odds;
  const hasGame = team.has_game_tonight ?? false;

  if (Math.abs(bestCase - worstCase) > 0.5) {
    const rangeLabel = el('div', { class: 'dive-section-label' });
    rangeLabel.textContent = '🌙 Tonight\'s Range';
    container.appendChild(rangeLabel);

    const rangeSection = el('div', { class: 'tonights-range fade-in' });
    rangeSection.innerHTML = `
      <div class="range-box range-worst">
        <div class="range-odds-big odds-low">${worstCase.toFixed(0)}%</div>
        <div class="range-box-label">WORST CASE</div>
        <div class="range-box-desc">${hasGame ? 'Lose reg + rivals win reg' : 'Rivals all win reg'}</div>
      </div>
      <div class="range-box range-medium">
        <div class="range-odds-big odds-mid">${mediumCase.toFixed(0)}%</div>
        <div class="range-box-label">MEDIUM CASE</div>
        <div class="range-box-desc">${hasGame ? 'Win OT + rivals win OT' : 'Everyone wins OT (pts spread)'}</div>
      </div>
      <div class="range-box range-best">
        <div class="range-odds-big odds-high">${bestCase.toFixed(0)}%</div>
        <div class="range-box-label">BEST CASE</div>
        <div class="range-box-desc">${hasGame ? 'Win reg + rivals lose reg' : 'Rivals all lose reg'}</div>
      </div>`;
    container.appendChild(rangeSection);
  }

  // ── Scenario cards ──
  const scenarios = team.game_scenarios || [];

  const scLabel = el('div', { class: 'dive-section-label' });
  scLabel.textContent = '📅 How Today\'s Games Affect You';
  container.appendChild(scLabel);

  if (!scenarios.length) {
    const noSc = el('div', { class: 'no-scenarios' });
    noSc.textContent = 'No games today — or scenario data not yet available.';
    container.appendChild(noSc);
  } else {
    const grid = el('div', { class: 'scenarios-grid' });

    scenarios.forEach(sc => {
      const card = el('div', { class: `scenario-card impact-${sc.impact} fade-in` });

      const homeAbbrev = sc.home_team;
      const awayAbbrev = sc.away_team;

      // Build 4 outcome rows: home reg, away reg, home OT, away OT
      const rows = [
        {
          label: `🏆 <strong>${esc(homeAbbrev)}</strong> wins (reg)`,
          pct: sc.if_home_reg_win_pct ?? sc.if_home_wins_pct,
          delta: sc.home_delta_reg ?? sc.home_delta ?? 0,
          ot: false,
        },
        {
          label: `🏒 <strong>${esc(homeAbbrev)}</strong> wins (OT)`,
          pct: sc.if_home_ot_win_pct ?? sc.if_home_reg_win_pct ?? sc.if_home_wins_pct,
          delta: sc.home_ot_delta ?? sc.home_delta_reg ?? 0,
          ot: true,
        },
        {
          label: `🏒 <strong>${esc(awayAbbrev)}</strong> wins (OT)`,
          pct: sc.if_away_ot_win_pct ?? sc.if_away_reg_win_pct ?? sc.if_away_wins_pct,
          delta: sc.away_ot_delta ?? sc.away_delta_reg ?? 0,
          ot: true,
        },
        {
          label: `❌ <strong>${esc(awayAbbrev)}</strong> wins (reg)`,
          pct: sc.if_away_reg_win_pct ?? sc.if_away_wins_pct,
          delta: sc.away_delta_reg ?? sc.away_delta ?? 0,
          ot: false,
        },
      ];

      // Sort: best outcome for selected team first (highest pct / delta)
      rows.sort((a, b) => b.pct - a.pct);

      const rowsHtml = rows.map(r => `
        <div class="scenario-row${r.ot ? ' ot-result' : ''}">
          <div class="scenario-row-label">${r.label}</div>
          <div class="scenario-row-right">
            <span class="scenario-pct">${(r.pct ?? 0).toFixed(1)}%</span>
            <span class="scenario-delta ${deltaClass(r.delta)}">${deltaStr(r.delta)}</span>
          </div>
        </div>`).join('');

      card.innerHTML = `
        <div class="scenario-matchup">
          <div class="s-team">
            <img class="s-logo" src="${logoUrl(awayAbbrev)}" alt="${awayAbbrev}" onerror="this.style.display='none'">
            ${esc(awayAbbrev)}
          </div>
          <span class="s-at">@</span>
          <div class="s-team">
            <img class="s-logo" src="${logoUrl(homeAbbrev)}" alt="${homeAbbrev}" onerror="this.style.display='none'">
            ${esc(homeAbbrev)}
          </div>
        </div>
        <div class="scenario-rows">${rowsHtml}</div>`;

      grid.appendChild(card);
    });

    container.appendChild(grid);
  }

  // ── Path to Playoffs ──
  const pathLabel = el('div', { class: 'dive-section-label' });
  pathLabel.textContent = '🗺️ Your Path';
  container.appendChild(pathLabel);

  const pathCard = el('div', { class: 'dive-path-card fade-in' });
  pathCard.innerHTML = buildPathSummary(team, scenarios);
  container.appendChild(pathCard);

  // ── What If Table ──
  const whatIf = team.what_if || [];
  if (whatIf.length) {
    const totalSims = team.what_if_total_sims || 10000;
    const wifLabel = el('div', { class: 'dive-section-label' });
    wifLabel.textContent = '🤔 What If They Finish...';
    container.appendChild(wifLabel);

    const wifTable = el('table', { class: 'what-if-table fade-in' });
    wifTable.innerHTML = `
      <thead>
        <tr>
          <th>Record</th>
          <th># Times</th>
          <th>Pts</th>
          <th>Make Playoffs</th>
          <th>Miss Playoffs</th>
        </tr>
      </thead>`;

    const tbody = el('tbody');
    const gl = team.gamesRemaining ?? 0;
    const currentPace = Math.round(gl * (team.wins / (team.gamesPlayed || 1)));

    whatIf.forEach(row => {
      const recordStr = `${row.wins}-${row.losses}-${row.otl}`;
      const times = row.times || 0;
      const pct = row.playoff_pct;
      const missPct = (100 - pct).toFixed(1);
      const makeColor = pct >= 75 ? 'odds-high' : pct >= 25 ? 'odds-mid' : 'odds-low';
      const missColor = pct <= 25 ? 'odds-low' : pct <= 75 ? 'odds-mid' : 'odds-high';
      const barColor = pct >= 75 ? 'var(--green)' : pct >= 25 ? 'var(--yellow)' : 'var(--red)';

      const isCurrentPace = row.wins === currentPace && row.otl === 0;

      const tr = el('tr');
      if (isCurrentPace) tr.classList.add('what-if-pace');
      // Dim rows with very few occurrences
      if (times <= 5) tr.style.opacity = '0.5';

      tr.innerHTML = `
        <td class="wif-record">${recordStr}${isCurrentPace ? ' <span class="wif-pace-badge">PACE</span>' : ''}</td>
        <td class="wif-times">${times.toLocaleString()}</td>
        <td class="wif-pts">${row.final_points}</td>
        <td class="wif-make">
          <div class="wif-bar-wrap">
            <div class="wif-bar" style="width:${Math.min(100, pct)}%;background:${barColor}"></div>
          </div>
          <span class="${makeColor}">${pct.toFixed(1)}%</span>
        </td>
        <td class="wif-miss"><span class="${missColor}">${missPct}%</span></td>`;
      tbody.appendChild(tr);
    });

    wifTable.appendChild(tbody);
    container.appendChild(wifTable);

    const wifNote = el('p', { style: 'font-size:11px;color:var(--text-muted);margin-top:6px;' });
    wifNote.textContent = `Based on 500,000 simulations. # Times = how often this exact record occurred.`;
    container.appendChild(wifNote);
  }

  // ── Tomorrow's Games section with full scenarios ──
  const tmrScenarios = team.tomorrow_scenarios || [];
  if (tmrScenarios.length) {
    const tmrLabel = el('div', { class: 'dive-section-label' });
    tmrLabel.textContent = '📅 How Tomorrow\'s Games Affect You';
    container.appendChild(tmrLabel);

    // Tomorrow's range
    const tmrBest = team.best_case_tomorrow ?? odds;
    const tmrWorst = team.worst_case_tomorrow ?? odds;
    if (tmrBest !== tmrWorst) {
      const tmrRange = el('div', { class: 'range-section fade-in' });
      const hasGameTmr = team.has_game_tomorrow ?? false;
      tmrRange.innerHTML = `
        <div class="range-box worst">
          <div class="range-pct">${tmrWorst.toFixed(1)}%</div>
          <div class="range-label">${hasGameTmr ? 'Lose + rivals win' : 'Rivals win'}</div>
        </div>
        <div class="range-box current">
          <div class="range-pct">${odds.toFixed(1)}%</div>
          <div class="range-label">Current</div>
        </div>
        <div class="range-box best">
          <div class="range-pct">${tmrBest.toFixed(1)}%</div>
          <div class="range-label">${hasGameTmr ? 'Win + rivals lose' : 'Rivals lose'}</div>
        </div>`;
      container.appendChild(tmrRange);
    }

    const tmrGrid = el('div', { class: 'scenarios-grid' });
    
    tmrScenarios.forEach(sc => {
      const card = el('div', { class: `scenario-card impact-${sc.impact} fade-in tomorrow-card` });
      const homeAbbrev = sc.home_team;
      const awayAbbrev = sc.away_team;

      const rows = [
        {
          label: `🏆 <strong>${esc(homeAbbrev)}</strong> wins (reg)`,
          pct: sc.if_home_reg_win_pct ?? sc.if_home_wins_pct,
          delta: sc.home_delta_reg ?? sc.home_delta ?? 0,
          ot: false,
        },
        {
          label: `🏒 <strong>${esc(homeAbbrev)}</strong> wins (OT)`,
          pct: sc.if_home_ot_win_pct ?? sc.if_home_reg_win_pct ?? sc.if_home_wins_pct,
          delta: sc.home_ot_delta ?? sc.home_delta_reg ?? 0,
          ot: true,
        },
        {
          label: `🏒 <strong>${esc(awayAbbrev)}</strong> wins (OT)`,
          pct: sc.if_away_ot_win_pct ?? sc.if_away_reg_win_pct ?? sc.if_away_wins_pct,
          delta: sc.away_ot_delta ?? sc.away_delta_reg ?? 0,
          ot: true,
        },
        {
          label: `❌ <strong>${esc(awayAbbrev)}</strong> wins (reg)`,
          pct: sc.if_away_reg_win_pct ?? sc.if_away_wins_pct,
          delta: sc.away_delta_reg ?? sc.away_delta ?? 0,
          ot: false,
        },
      ];

      rows.sort((a, b) => b.pct - a.pct);

      const rowsHtml = rows.map(r => `
        <div class="scenario-row${r.ot ? ' ot-result' : ''}">
          <div class="scenario-row-label">${r.label}</div>
          <div class="scenario-row-right">
            <span class="scenario-pct">${(r.pct ?? 0).toFixed(1)}%</span>
            <span class="scenario-delta ${deltaClass(r.delta)}">${deltaStr(r.delta)}</span>
          </div>
        </div>`).join('');

      card.innerHTML = `
        <div class="scenario-matchup">
          <div class="s-team">
            <img class="s-logo" src="${logoUrl(awayAbbrev)}" alt="${awayAbbrev}" onerror="this.style.display='none'">
            ${esc(awayAbbrev)}
          </div>
          <span class="s-at">@</span>
          <div class="s-team">
            <img class="s-logo" src="${logoUrl(homeAbbrev)}" alt="${homeAbbrev}" onerror="this.style.display='none'">
            ${esc(homeAbbrev)}
          </div>
        </div>
        <div class="scenario-rows">${rowsHtml}</div>`;

      tmrGrid.appendChild(card);
    });

    container.appendChild(tmrGrid);
  }
}

function buildPathSummary(team, scenarios) {
  const name = team.teamCommonName || team.teamName || team.teamAbbrev;
  const odds = team.playoffOdds ?? 0;
  const gamesLeft = team.gamesRemaining ?? 0;

  if (team.clinched) {
    return `<div class="path-icon">🏆</div>
      <div class="path-text">The <strong>${esc(name)}</strong> have clinched a playoff spot.
      Time to focus on seeding.</div>`;
  }

  if (team.eliminated) {
    return `<div class="path-icon">📵</div>
      <div class="path-text">The <strong>${esc(name)}</strong> have been eliminated from playoff contention.
      Looking ahead to the draft lottery.</div>`;
  }

  // Estimate wins needed: rough heuristic
  const ptsPerWin = 2;
  const currentPts = team.points ?? 0;

  // Find the approximate bubble threshold: 8th place in their conference by points
  // (we don't have conference data here directly, so use a rough heuristic)
  const ptsNeededEst = Math.max(0, Math.round((100 - odds) / 10));
  const winsNeeded = Math.ceil(gamesLeft * ((100 - odds) / 200));

  // Count high-impact games today
  const highImpact = scenarios.filter(s => s.impact === 'high').length;
  const medImpact = scenarios.filter(s => s.impact === 'medium').length;

  // Determine favorable games (games where the away-win scenario is better)
  const helpNeeded = scenarios.filter(s => {
    const baseline = odds;
    return (s.away_delta > 1 || s.home_delta > 1);
  }).length;

  let summary = `<div class="path-icon">🛤️</div><div class="dive-path-text">`;

  if (odds >= 90) {
    summary += `<strong>${esc(name)}</strong> are in a strong position with <strong>${odds.toFixed(0)}%</strong> playoff odds. 
      With <strong>${gamesLeft}</strong> games left, they just need to keep the pace.`;
  } else if (odds >= 70) {
    summary += `<strong>${esc(name)}</strong> sit at <strong>${odds.toFixed(0)}%</strong> odds with 
      <strong>${gamesLeft}</strong> games remaining. A winning record down the stretch should seal it.`;
  } else if (odds >= 40) {
    const winsTarget = Math.ceil(gamesLeft * 0.55);
    summary += `<strong>${esc(name)}</strong> are squarely in the bubble at <strong>${odds.toFixed(0)}%</strong>. 
      They need roughly <strong>${winsTarget} wins</strong> in their last <strong>${gamesLeft} games</strong>`;
    if (helpNeeded > 0) {
      summary += ` and will benefit from help in <strong>${helpNeeded} of today's games</strong>`;
    }
    summary += `.`;
  } else if (odds >= 15) {
    const winsTarget = Math.ceil(gamesLeft * 0.7);
    summary += `It's an uphill climb for <strong>${esc(name)}</strong> at <strong>${odds.toFixed(0)}%</strong>. 
      They likely need <strong>${winsTarget}+ wins</strong> in their final <strong>${gamesLeft} games</strong> 
      AND significant help from other results to sneak in.`;
  } else {
    summary += `<strong>${esc(name)}</strong> sit at just <strong>${odds.toFixed(0)}%</strong> — 
      a near-mathematical long shot with <strong>${gamesLeft}</strong> games left.`;
  }

  if (highImpact > 0) {
    summary += ` <strong>${highImpact} of today's game${highImpact > 1 ? 's' : ''}</strong> 
      could swing their odds significantly.`;
  }

  summary += `</div>`;
  return summary;
}

function deltaClass(d) {
  if (d >= 0.5) return 'pos';
  if (d <= -0.5) return 'neg';
  return 'neutral';
}

function deltaStr(d) {
  if (Math.abs(d) < 0.1) return '—';
  const arrow = d > 0 ? '▲' : '▼';
  return `${arrow}${Math.abs(d).toFixed(1)}%`;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function renderSectionTitle(icon, title, badge) {
  const div = el('div', { class: 'section-title' });
  div.innerHTML = `<span class="icon">${icon}</span>${esc(title)}${badge ? `<span style="font-size:13px;font-weight:400;color:var(--text-muted);margin-left:6px">${badge}</span>` : ''}`;
  return div;
}

function el(tag, attrs = {}) {
  const elem = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => elem.setAttribute(k, v));
  return elem;
}

function esc(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function logoUrl(abbrev) {
  const theme = document.documentElement.dataset.theme || 'light';
  const suffix = theme === 'dark' ? '_light' : '_dark';
  return `${NHL_LOGO_BASE}${abbrev}${suffix}.svg`;
}

// Always use light logos inside dark strip
function logoUrlDark(abbrev) {
  return `${NHL_LOGO_BASE}${abbrev}_light.svg`;
}

function oddsClass(pct) {
  if (pct >= 75) return 'odds-high';
  if (pct >= 25) return 'odds-mid';
  return 'odds-low';
}

function oddsBarClass(pct) {
  if (pct >= 75) return 'bar-high';
  if (pct >= 25) return 'bar-mid';
  return 'bar-low';
}

function oddsStyle(pct) {
  if (pct >= 75) return 'background:rgba(63,185,80,0.15);color:var(--green)';
  if (pct >= 25) return 'background:rgba(210,153,34,0.15);color:var(--yellow)';
  return 'background:rgba(248,81,73,0.15);color:var(--red)';
}

function formatTime(date) {
  return date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true }) +
    ' ' + date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function formatGameTime(utcStr) {
  if (!utcStr) return 'Time TBD';
  try {
    const dt = new Date(utcStr);
    return dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, timeZoneName: 'short' });
  } catch { return utcStr; }
}

// ─── Theme Toggle ─────────────────────────────────────────────────────────────

function toggleTheme() {
  const html = document.documentElement;
  const btn = document.getElementById('theme-toggle');
  if (html.dataset.theme === 'light') {
    html.dataset.theme = 'dark';
    if (btn) btn.textContent = '☀️ Light';
    localStorage.setItem('icechaser-theme', 'dark');
  } else {
    html.dataset.theme = 'light';
    if (btn) btn.textContent = '🌙 Dark';
    localStorage.setItem('icechaser-theme', 'light');
  }
  if (currentData) {
    const prev = getPrevOdds();
    render(currentData, prev);
  }
}

(function() {
  const saved = localStorage.getItem('icechaser-theme');
  if (saved === 'dark') {
    document.documentElement.dataset.theme = 'dark';
    const btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = '☀️ Light';
  }
})();

// ─── Standings Table (SportsClubStats-style) ──────────────────────────────────

function renderStandingsTable(conferences, teams, todaysGames) {
  const section = el('section', { class: 'section standings-section' });
  section.appendChild(renderSectionTitle('📊', 'Full Standings'));

  const table = el('table', { class: 'standings-table' });

  const hGroup = el('tr', { class: 'header-group' });
  hGroup.innerHTML = `
    <td colspan="2"></td>
    <td colspan="5"></td>
    <td colspan="2">Make Playoffs</td>
    <td colspan="2" class="range-bracket">Tonight's Range</td>
    <td></td>`;
  table.appendChild(hGroup);

  const hCols = document.createElement('tr');
  hCols.className = 'header-cols';
  hCols.innerHTML = `
    <th class="al" style="width:130px">Tonight</th>
    <th class="al" style="width:140px">Team</th>
    <th>PTS</th>
    <th>GP</th>
    <th>W-L-OT</th>
    <th>GD</th>
    <th>GL</th>
    <th style="min-width:56px">%</th>
    <th>Chg</th>
    <th style="color:var(--green)" title="Playoff odds if all tonight's games go this team's way">Best</th>
    <th style="color:var(--red)" title="Playoff odds if all tonight's games go against this team">Worst</th>
    <th class="al">Next</th>`;
  table.appendChild(hCols);

  const gamesByTeam = {};
  (todaysGames || []).forEach(g => {
    const homeState = g.gameState;
    const isFinal = (homeState === 'FINAL' || homeState === 'OFF');
    const isLive = (homeState === 'LIVE' || homeState === 'CRIT');
    if (isFinal) {
      const homeWon = g.homeScore > g.awayScore;
      gamesByTeam[g.homeTeamAbbrev] = {
        text: `${homeWon ? 'W' : 'L'} vs ${g.awayTeamAbbrev} ${homeWon ? g.homeScore+'-'+g.awayScore : g.awayScore+'-'+g.homeScore}`,
        win: homeWon
      };
      gamesByTeam[g.awayTeamAbbrev] = {
        text: `${!homeWon ? 'W' : 'L'} @ ${g.homeTeamAbbrev} ${!homeWon ? g.awayScore+'-'+g.homeScore : g.homeScore+'-'+g.awayScore}`,
        win: !homeWon
      };
    } else if (isLive) {
      gamesByTeam[g.homeTeamAbbrev] = { text: `LIVE vs ${g.awayTeamAbbrev} ${g.homeScore}-${g.awayScore}`, win: null };
      gamesByTeam[g.awayTeamAbbrev] = { text: `LIVE @ ${g.homeTeamAbbrev} ${g.awayScore}-${g.homeScore}`, win: null };
    } else {
      gamesByTeam[g.homeTeamAbbrev] = { text: `vs ${g.awayTeamAbbrev}`, win: null };
      gamesByTeam[g.awayTeamAbbrev] = { text: `@ ${g.homeTeamAbbrev}`, win: null };
    }
  });

  const teamMap = {};
  (teams || []).forEach(t => { teamMap[t.teamAbbrev] = t; });

  ['Eastern', 'Western'].forEach(confName => {
    const conf = conferences[confName];
    if (!conf) return;

    const confRow = el('tr', { class: 'conf-row' });
    confRow.innerHTML = `<td colspan="12">${confName === 'Eastern' ? '🔵' : '🟣'} ${confName} Conference</td>`;
    table.appendChild(confRow);

    const divisions = conf.divisions || {};
    const wildcards = conf.wildcards || [];

    Object.entries(divisions).forEach(([divName, divTeams]) => {
      const divRow = el('tr', { class: 'div-row' });
      divRow.innerHTML = `<td colspan="12">${divName} Division</td>`;
      table.appendChild(divRow);
      (divTeams || []).forEach(team => {
        table.appendChild(buildStandingsRow(team, teamMap, gamesByTeam));
      });
    });

    if (wildcards.length) {
      const wcRow = el('tr', { class: 'wc-row' });
      wcRow.innerHTML = '<td colspan="12"></td>';
      table.appendChild(wcRow);
      wildcards.forEach(team => {
        table.appendChild(buildStandingsRow(team, teamMap, gamesByTeam));
      });
    }
  });

  section.appendChild(table);

  const note = el('p', { style: 'margin-top:8px;font-size:12px;color:var(--text-muted)' });
  note.textContent = 'Probabilities from 500,000 Monte Carlo simulations. Tonight\'s Range = playoff odds after best/worst possible outcomes tonight. GL = Games Left. Click team for deep dive.';
  section.appendChild(note);

  return section;
}

function buildStandingsRow(team, teamMap, gamesByTeam) {
  const abbrev = team.teamAbbrev;
  const full = teamMap[abbrev] || team;
  const odds = full.playoffOdds ?? 0;
  const gd = (full.goalsFor || 0) - (full.goalsAgainst || 0);
  const record = `${full.wins}-${full.losses}-${full.otLosses}`;

  const row = el('tr', { class: 'team-data' });
  if (full.eliminated) row.classList.add('eliminated');
  if (full.clinched) row.classList.add('clinched');

  const gameInfo = gamesByTeam[abbrev];
  let resultHtml = '<td class="col-result">—</td>';
  if (gameInfo) {
    const cls = gameInfo.win === true ? 'win' : (gameInfo.win === false ? 'loss' : '');
    resultHtml = `<td class="col-result ${cls}">${esc(gameInfo.text)}</td>`;
  }

  let badge = '';
  if (full.clinched) badge = '<span class="badge badge-x">x</span>';
  else if (full.eliminated) badge = '<span class="badge badge-e">e</span>';

  let pctClass = 'mid';
  if (odds >= 99.5) pctClass = 'safe';
  else if (odds >= 75) pctClass = 'high';
  else if (odds <= 15) pctClass = 'low';

  const pctHtml = odds <= 0 && full.eliminated
    ? `<td class="col-pct"><span class="pct-num dead">0</span></td>`
    : `<td class="col-pct"><div class="pct-bar ${pctClass}" style="width:${Math.min(100, odds)}%"></div><span class="pct-num ${pctClass}">${odds.toFixed(0)}</span></td>`;

  const prevOdds = getPrevOdds();
  const prevPct = prevOdds?.odds?.[abbrev] ?? null;
  const change = prevPct !== null ? odds - prevPct : null;
  let changeHtml = '<td class="col-change flat">—</td>';
  if (change !== null && Math.abs(change) >= 0.5) {
    const cls = change > 0 ? 'pos' : 'neg';
    const sign = change > 0 ? '+' : '';
    changeHtml = `<td class="col-change ${cls}">${sign}${change.toFixed(0)}</td>`;
  }

  const best = full.best_case_tonight ?? odds;
  const worst = full.worst_case_tonight ?? odds;
  const bestHtml = (full.eliminated || full.clinched) ? '<td class="col-best">—</td>' : `<td class="col-best">${best.toFixed(0)}</td>`;
  const worstHtml = (full.eliminated || full.clinched) ? '<td class="col-worst">—</td>' : `<td class="col-worst">${worst.toFixed(0)}</td>`;

  const gdClass = gd > 0 ? 'pos' : (gd < 0 ? 'neg' : '');
  const gdStr = gd > 0 ? `+${gd}` : `${gd}`;

  row.innerHTML = `
    ${resultHtml}
    <td class="col-team"><img class="logo-tiny" src="${logoUrl(abbrev)}" alt="${abbrev}" onerror="this.style.display='none'"><a href="#" onclick="selectTeamDive('${abbrev}');return false;">${esc(full.teamCommonName || full.teamName || abbrev)}</a>${badge}</td>
    <td class="col-pts">${full.points}</td>
    <td>${full.gamesPlayed}</td>
    <td>${record}</td>
    <td class="col-gd ${gdClass}">${gdStr}</td>
    <td>${full.gamesRemaining}</td>
    ${pctHtml}
    ${changeHtml}
    ${bestHtml}
    ${worstHtml}
    <td class="col-next">—</td>`;

  return row;
}

function selectTeamDive(abbrev) {
  const select = document.getElementById('team-select');
  if (select) {
    select.value = abbrev;
    select.dispatchEvent(new Event('change'));
    const dive = document.getElementById('team-dive');
    if (dive) dive.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

// ─── Start ────────────────────────────────────────────────────────────────────
init();
