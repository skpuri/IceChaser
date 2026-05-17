// ─── Odds Explorer — IceChaser (NHL) ─────────────────────────────────────────
// Drop-in addition to app.js. Uses raw SVG, no extra dependencies.
// Reads from /data/odds_history.json (written by generate_data_v3.py).
// Opened by clicking the expand button on the Odds Movement chart header.

var OddsExplorer = (function() {
  'use strict';

  // ── NHL team metadata ──
  var TEAMS = {
    // Atlantic
    FLA:{name:"Panthers",div:"Atlantic",lg:"East",color:"#041E42"},
    TOR:{name:"Maple Leafs",div:"Atlantic",lg:"East",color:"#00205B"},
    BOS:{name:"Bruins",div:"Atlantic",lg:"East",color:"#FFB81C"},
    TBL:{name:"Lightning",div:"Atlantic",lg:"East",color:"#002868"},
    BUF:{name:"Sabres",div:"Atlantic",lg:"East",color:"#003087"},
    DET:{name:"Red Wings",div:"Atlantic",lg:"East",color:"#CE1126"},
    OTT:{name:"Senators",div:"Atlantic",lg:"East",color:"#C52032"},
    MTL:{name:"Canadiens",div:"Atlantic",lg:"East",color:"#AF1E2D"},
    // Metropolitan
    WSH:{name:"Capitals",div:"Metropolitan",lg:"East",color:"#041E42"},
    NJD:{name:"Devils",div:"Metropolitan",lg:"East",color:"#CE1126"},
    CAR:{name:"Hurricanes",div:"Metropolitan",lg:"East",color:"#CC0000"},
    NYR:{name:"Rangers",div:"Metropolitan",lg:"East",color:"#0038A8"},
    NYI:{name:"Islanders",div:"Metropolitan",lg:"East",color:"#00539B"},
    PIT:{name:"Penguins",div:"Metropolitan",lg:"East",color:"#FCB514"},
    PHI:{name:"Flyers",div:"Metropolitan",lg:"East",color:"#F74902"},
    CBJ:{name:"Blue Jackets",div:"Metropolitan",lg:"East",color:"#002654"},
    // Central
    WPG:{name:"Jets",div:"Central",lg:"West",color:"#041E42"},
    DAL:{name:"Stars",div:"Central",lg:"West",color:"#006847"},
    COL:{name:"Avalanche",div:"Central",lg:"West",color:"#6F263D"},
    MIN:{name:"Wild",div:"Central",lg:"West",color:"#154734"},
    STL:{name:"Blues",div:"Central",lg:"West",color:"#002F87"},
    NSH:{name:"Predators",div:"Central",lg:"West",color:"#FFB81C"},
    CHI:{name:"Blackhawks",div:"Central",lg:"West",color:"#CF0A2C"},
    UTA:{name:"Utah HC",div:"Central",lg:"West",color:"#6CACE4"},
    // Pacific
    VGK:{name:"Golden Knights",div:"Pacific",lg:"West",color:"#B4975A"},
    EDM:{name:"Oilers",div:"Pacific",lg:"West",color:"#041E42"},
    LAK:{name:"Kings",div:"Pacific",lg:"West",color:"#111111"},
    VAN:{name:"Canucks",div:"Pacific",lg:"West",color:"#00205B"},
    CGY:{name:"Flames",div:"Pacific",lg:"West",color:"#C8102E"},
    SEA:{name:"Kraken",div:"Pacific",lg:"West",color:"#001628"},
    SJS:{name:"Sharks",div:"Pacific",lg:"West",color:"#006D75"},
    ANA:{name:"Ducks",div:"Pacific",lg:"West",color:"#F47A38"},
  };
  var DIVS = ["Atlantic","Metropolitan","Central","Pacific"];

  // ── State ──
  var state = {
    data: null,
    allAbbrevs: [],
    visible: [],
    filterSet: [],
    filter: 'all',
    hovered: null,
    compareMode: false,
    compareTeams: [],
    dateRange: [0, 0],
    overlay: null,
  };

  function getLatest(abbrev) {
    if (!state.data) return 0;
    var d = state.data.dates;
    for (var i = d.length-1; i >= 0; i--) {
      var v = state.data.teams[abbrev] && state.data.teams[abbrev][d[i]];
      if (v != null) return v;
    }
    return 0;
  }
  function isDark() { return document.documentElement.dataset.theme === 'dark'; }
  function fmtDate(s) {
    try { return new Date(s+'T12:00:00').toLocaleDateString('en-US',{month:'short',day:'numeric'}); }
    catch(e) { return s; }
  }

  // ── Public: open ──
  function open(historyData) {
    if (state.overlay) return;
    state.data = historyData;
    state.allAbbrevs = Object.keys(historyData.teams).sort(function(a,b) { return getLatest(b) - getLatest(a); });
    state.visible = state.allAbbrevs.slice();
    state.filterSet = state.allAbbrevs.slice();
    state.filter = 'all';
    state.hovered = null;
    state.compareMode = false;
    state.compareTeams = [];
    state.dateRange = [0, historyData.dates.length - 1];

    var overlay = document.createElement('div');
    overlay.className = 'explorer-overlay';
    overlay.innerHTML = buildHTML();
    document.body.appendChild(overlay);
    document.body.style.overflow = 'hidden';
    state.overlay = overlay;

    bindEvents();
    renderSVG();
    document.addEventListener('keydown', onEsc);
  }

  function close() {
    if (!state.overlay) return;
    state.overlay.remove();
    state.overlay = null;
    document.body.style.overflow = '';
    document.removeEventListener('keydown', onEsc);
  }
  function onEsc(e) { if (e.key === 'Escape') close(); }

  // ── HTML template ──
  function buildHTML() {
    return [
      '<div class="explorer-container">',
        '<div class="explorer-header">',
          '<div class="explorer-title"><span style="margin-right:6px">🏒</span>Odds Explorer</div>',
          '<div class="explorer-subtitle">NHL Playoff Probability Tracker</div>',
          '<div style="flex:1"></div>',
          '<button class="explorer-close-btn" id="explorer-close">✕ Close</button>',
        '</div>',
        '<div class="explorer-filters" id="explorer-filters"></div>',
        '<div class="explorer-compare-banner" id="explorer-compare-banner" style="display:none"></div>',
        '<div class="explorer-chart-wrap" id="explorer-chart-wrap">',
          '<svg id="explorer-svg" width="100%" height="100%"></svg>',
          '<div class="explorer-tooltip" id="explorer-tooltip" style="display:none"></div>',
          '<div class="explorer-cutoff-label">── playoff cutoff line</div>',
        '</div>',
        '<div class="explorer-slider-wrap" id="explorer-slider-wrap"></div>',
        '<div class="explorer-movers" id="explorer-movers"></div>',
        '<div class="explorer-chips-wrap">',
          '<div class="explorer-chips-label" id="explorer-chips-label">TEAMS — tap to toggle</div>',
          '<div class="explorer-chips" id="explorer-chips"></div>',
        '</div>',
        '<div class="explorer-footer" id="explorer-footer"></div>',
      '</div>',
    ].join('');
  }

  // ── Render filters ──
  function renderFilters() {
    var wrap = state.overlay.querySelector('#explorer-filters');
    var presets = [
      {key:'all',label:'All 32'},{key:'east',label:'Eastern'},{key:'west',label:'Western'},{key:'bubble',label:'Bubble'}
    ];
    var html = '';
    presets.forEach(function(p) {
      var cls = state.filter === p.key ? 'explorer-filter-btn active' : 'explorer-filter-btn';
      html += '<button class="'+cls+'" data-filter="'+p.key+'">'+p.label+'</button>';
    });
    html += '<span class="explorer-filter-sep"></span>';
    DIVS.forEach(function(d) {
      var cls = state.filter === d ? 'explorer-div-btn active' : 'explorer-div-btn';
      html += '<button class="'+cls+'" data-filter="'+d+'">'+d+'</button>';
    });
    html += '<span style="flex:1"></span>';
    var cCls = state.compareMode ? 'explorer-compare-btn active' : 'explorer-compare-btn';
    html += '<button class="'+cCls+'" id="explorer-compare-toggle">' +
      (state.compareMode ? 'COMPARE '+state.compareTeams.length+'/2' : '⚔ COMPARE') + '</button>';
    wrap.innerHTML = html;
  }

  function applyFilter(f) {
    state.filter = f;
    var all = state.allAbbrevs;
    if (f === 'east') state.visible = all.filter(function(a){return TEAMS[a] && TEAMS[a].lg==='East';});
    else if (f === 'west') state.visible = all.filter(function(a){return TEAMS[a] && TEAMS[a].lg==='West';});
    else if (f === 'bubble') state.visible = all.filter(function(a){var v=getLatest(a); return v>15&&v<85;});
    else if (DIVS.indexOf(f) !== -1) state.visible = all.filter(function(a){return TEAMS[a] && TEAMS[a].div===f;});
    else state.visible = all.slice();
    state.filterSet = state.visible.slice();
    renderFilters();
    renderChips();
    renderSVG();
    renderFooter();
  }

  // ── Team chips ──
  function renderChips() {
    var wrap = state.overlay.querySelector('#explorer-chips');
    var label = state.overlay.querySelector('#explorer-chips-label');
    label.textContent = state.compareMode ? 'TEAMS — tap to compare' : 'TEAMS — tap to toggle';
    var pool = state.filterSet.length ? state.filterSet : state.allAbbrevs;
    var sorted = pool.slice().sort(function(a,b){return getLatest(b)-getLatest(a);});
    var html = '';
    sorted.forEach(function(abbrev) {
      var t = TEAMS[abbrev] || {};
      var isVis = state.visible.indexOf(abbrev) !== -1;
      var isComp = state.compareTeams.indexOf(abbrev) !== -1;
      var cls = 'explorer-chip';
      if (isComp) cls += ' compare';
      else if (!isVis && !state.compareMode) cls += ' toggled-off';
      html += '<button class="'+cls+'" data-team="'+abbrev+'">' +
        '<span class="explorer-chip-dot" style="background:'+(t.color||'#888')+'"></span>' +
        '<span class="explorer-chip-abbrev">'+abbrev+'</span>' +
        '<span class="explorer-chip-pct">'+getLatest(abbrev).toFixed(0)+'%</span>' +
        '</button>';
    });
    wrap.innerHTML = html;
  }

  // ── Render big movers (net change over last 14 days) ──
  function renderMovers() {
    var wrap = state.overlay.querySelector('#explorer-movers');
    if (!state.data) { wrap.innerHTML = ''; return; }
    var dates = state.data.dates;
    if (dates.length < 2) { wrap.innerHTML = ''; return; }
    var latestDate = dates[dates.length - 1];
    var cutoff = new Date(); cutoff.setDate(cutoff.getDate() - 14);
    var cutoffStr = cutoff.toISOString().slice(0, 10);
    var baseIdx = 0;
    for (var si = 0; si < dates.length; si++) {
      if (dates[si] >= cutoffStr) { baseIdx = si; break; }
    }
    var baseDate = dates[baseIdx];
    var movers = [];
    Object.keys(state.data.teams).forEach(function(abbrev) {
      var baseVal = state.data.teams[abbrev][baseDate];
      var latestVal = state.data.teams[abbrev][latestDate];
      if (baseVal != null && latestVal != null) {
        var delta = latestVal - baseVal;
        if (Math.abs(delta) > 1) {
          movers.push({ team: abbrev, delta: delta, from: baseVal, to: latestVal });
        }
      }
    });
    movers.sort(function(a,b) { return Math.abs(b.delta) - Math.abs(a.delta); });
    movers = movers.slice(0, 5);
    if (!movers.length) { wrap.innerHTML = ''; return; }
    var rangeLabel = fmtDate(baseDate) + ' → ' + fmtDate(latestDate);
    var html = '<div class="explorer-movers-label">⚡ BIG MOVERS <span style="font-weight:400;opacity:.6;font-size:.85em">(' + rangeLabel + ')</span></div><div class="explorer-movers-list">';
    movers.forEach(function(m) {
      var t = TEAMS[m.team] || {};
      var cls = m.delta > 0 ? 'up' : 'down';
      html += '<div class="explorer-mover '+cls+'">' +
        '<span class="explorer-chip-dot" style="background:'+(t.color||'#888')+'"></span>' +
        '<strong>'+m.team+'</strong> ' +
        '<span class="explorer-mover-delta">'+(m.delta>0?'+':'')+m.delta.toFixed(1)+'%</span> ' +
        '<span style="font-size:10px;color:var(--text-dim)">'+m.from.toFixed(0)+'→'+m.to.toFixed(0)+'%</span>' +
        '</div>';
    });
    html += '</div>';
    wrap.innerHTML = html;
  }

  function renderFooter() {
    var wrap = state.overlay.querySelector('#explorer-footer');
    if (!state.data) return;
    wrap.textContent = 'Showing ' + state.visible.length + ' of ' + state.allAbbrevs.length +
      ' teams · ' + state.data.dates.length + ' days · ' +
      fmtDate(state.data.dates[0]) + ' – ' + fmtDate(state.data.dates[state.data.dates.length-1]);
  }

  // ── Date slider ──
  function renderSlider() {
    var wrap = state.overlay.querySelector('#explorer-slider-wrap');
    var dates = state.data.dates;
    var startPct = (state.dateRange[0] / Math.max(1, dates.length-1)) * 100;
    var endPct = (state.dateRange[1] / Math.max(1, dates.length-1)) * 100;
    wrap.innerHTML = '<div class="explorer-slider-labels">' +
      '<span>'+fmtDate(dates[state.dateRange[0]])+'</span>' +
      '<span style="color:var(--text-dim)">drag handles to zoom</span>' +
      '<span>'+fmtDate(dates[state.dateRange[1]])+'</span></div>' +
      '<div class="explorer-slider-track" id="explorer-slider-track">' +
        '<div class="explorer-slider-active" style="left:'+startPct+'%;width:'+(endPct-startPct)+'%"></div>' +
        '<div class="explorer-slider-handle" id="explorer-handle-start" style="left:'+startPct+'%"></div>' +
        '<div class="explorer-slider-handle" id="explorer-handle-end" style="left:'+endPct+'%"></div>' +
      '</div>';
  }

  // ── SVG Chart ──
  function renderSVG() {
    var chartWrap = state.overlay.querySelector('#explorer-chart-wrap');
    var svg = state.overlay.querySelector('#explorer-svg');
    var rect = chartWrap.getBoundingClientRect();
    var W = rect.width;
    var H = rect.height;
    var margin = {top:16, right:12, bottom:32, left:42};
    var w = W - margin.left - margin.right;
    var h = H - margin.top - margin.bottom;
    if (w < 50 || h < 50) return;

    var dates = state.data.dates.slice(state.dateRange[0], state.dateRange[1]+1);
    if (!dates.length) return;

    var xStep = w / Math.max(1, dates.length-1);
    function xPos(i) { return margin.left + i * xStep; }
    function yPos(v) { return margin.top + h - (v/100) * h; }

    var dk = isDark();
    var gridColor = dk ? 'rgba(48,54,61,0.6)' : 'rgba(200,206,212,0.6)';
    var tickColor = dk ? '#484f58' : '#6c757d';

    var html = '';

    // Grid + Y labels
    [0,25,50,75,100].forEach(function(v) {
      html += '<line x1="'+margin.left+'" x2="'+(W-margin.right)+'" y1="'+yPos(v)+'" y2="'+yPos(v)+'" stroke="'+gridColor+'" stroke-width="1"/>';
      html += '<text x="'+(margin.left-8)+'" y="'+yPos(v)+'" dy="0.35em" text-anchor="end" fill="'+tickColor+'" font-size="11" font-family="inherit">'+v+'%</text>';
    });

    // X labels
    var maxTicks = Math.floor(w / 70);
    var step = Math.max(1, Math.ceil(dates.length / maxTicks));
    dates.forEach(function(d,i) {
      if (i % step === 0) {
        html += '<text x="'+xPos(i)+'" y="'+(H-4)+'" text-anchor="middle" fill="'+tickColor+'" font-size="10" font-family="inherit">'+fmtDate(d)+'</text>';
      }
    });

    // Playoff cutoff line — NHL: 16 teams make playoffs (top 3 per division + 2 WC per conference)
    var cutoffPath = [];
    dates.forEach(function(d,i) {
      var vals = state.allAbbrevs.map(function(a){return state.data.teams[a] && state.data.teams[a][d];}).filter(function(v){return v!=null;});
      vals.sort(function(a,b){return b-a;});
      var threshold = vals[15] != null ? vals[15] : 50;
      cutoffPath.push((i===0?'M':'L')+xPos(i).toFixed(1)+','+yPos(threshold).toFixed(1));
    });
    if (cutoffPath.length > 1) {
      html += '<path d="'+cutoffPath.join(' ')+'" fill="none" stroke="rgba(210,153,34,0.35)" stroke-width="1.5" stroke-dasharray="6 4"/>';
    }

    // Compare delta fill
    var isCompare = state.compareMode && state.compareTeams.length === 2;
    if (isCompare) {
      var ca = state.compareTeams[0], cb = state.compareTeams[1];
      var upper = [], lower = [];
      dates.forEach(function(d,i) {
        var va = state.data.teams[ca] && state.data.teams[ca][d];
        var vb = state.data.teams[cb] && state.data.teams[cb][d];
        if (va != null && vb != null) {
          upper.push(xPos(i).toFixed(1)+','+yPos(Math.max(va,vb)).toFixed(1));
          lower.push(xPos(i).toFixed(1)+','+yPos(Math.min(va,vb)).toFixed(1));
        }
      });
      if (upper.length) {
        var fillColor = dk ? 'rgba(88,166,255,0.12)' : 'rgba(13,110,253,0.08)';
        html += '<polygon points="'+upper.join(' ')+' '+lower.reverse().join(' ')+'" fill="'+fillColor+'"/>';
      }
    }

    // Team lines
    var drawTeams = isCompare ? state.compareTeams : state.visible;
    drawTeams.forEach(function(abbrev) {
      var t = TEAMS[abbrev] || {};
      var pts = [];
      dates.forEach(function(d,i) {
        var v = state.data.teams[abbrev] && state.data.teams[abbrev][d];
        if (v != null) pts.push((pts.length===0?'M':'L')+xPos(i).toFixed(1)+','+yPos(v).toFixed(1));
      });
      if (pts.length < 2) return;
      var isHov = state.hovered === abbrev;
      var isCompTeam = isCompare && state.compareTeams.indexOf(abbrev) !== -1;
      var dimmed = (state.hovered && !isHov && !isCompare) || (isCompare && !isCompTeam);
      var sw = isHov || isCompTeam ? 3.5 : 1.5;
      var op = dimmed ? 0.12 : (isHov || isCompTeam ? 1 : 0.55);
      html += '<path d="'+pts.join(' ')+'" fill="none" stroke="'+(t.color||'#888')+'" stroke-width="'+sw+'" opacity="'+op+'"/>';
    });

    html += '<rect x="'+margin.left+'" y="'+margin.top+'" width="'+w+'" height="'+h+'" fill="transparent" id="explorer-hover-rect"/>';
    svg.setAttribute('viewBox', '0 0 '+W+' '+H);
    svg.innerHTML = html;
    svg._layout = {margin:margin, w:w, h:h, W:W, H:H, dates:dates, xStep:xStep};
  }

  // ── Mouse events ──
  function onChartMouseMove(e) {
    var svg = state.overlay.querySelector('#explorer-svg');
    var L = svg._layout;
    if (!L) return;
    var rect = svg.getBoundingClientRect();
    var mx = (e.clientX - rect.left) * (L.W / rect.width);
    var my = (e.clientY - rect.top) * (L.H / rect.height);
    var localX = mx - L.margin.left;

    var idx = Math.round(localX / L.xStep);
    idx = Math.max(0, Math.min(L.dates.length-1, idx));
    var nearDate = L.dates[idx];

    var drawTeams = (state.compareMode && state.compareTeams.length===2) ? state.compareTeams : state.visible;
    var nearTeam = null;
    var nearDist = 30 * (L.H / rect.height);
    drawTeams.forEach(function(abbrev) {
      var v = state.data.teams[abbrev] && state.data.teams[abbrev][nearDate];
      if (v == null) return;
      var yVal = L.margin.top + L.h - (v/100)*L.h;
      var dist = Math.abs(yVal - my);
      if (dist < nearDist) { nearDist = dist; nearTeam = abbrev; }
    });

    state.hovered = nearTeam;
    renderSVG();
    showTooltip(e.clientX - rect.left, e.clientY - rect.top, nearDate, drawTeams);

    // Crosshair + dot
    var svgEl = state.overlay.querySelector('#explorer-svg');
    var crossX = L.margin.left + idx * L.xStep;
    var cl = document.createElementNS('http://www.w3.org/2000/svg','line');
    cl.setAttribute('x1', crossX); cl.setAttribute('x2', crossX);
    cl.setAttribute('y1', L.margin.top); cl.setAttribute('y2', L.margin.top + L.h);
    cl.setAttribute('stroke', dk ? 'rgba(139,148,158,0.4)' : 'rgba(100,100,100,0.3)');
    cl.setAttribute('stroke-width', '1'); cl.setAttribute('stroke-dasharray', '4 3');
    svgEl.appendChild(cl);

    if (nearTeam) {
      var val = state.data.teams[nearTeam][nearDate];
      if (val != null) {
        var dot = document.createElementNS('http://www.w3.org/2000/svg','circle');
        dot.setAttribute('cx', crossX);
        dot.setAttribute('cy', L.margin.top + L.h - (val/100)*L.h);
        dot.setAttribute('r', 5);
        dot.setAttribute('fill', (TEAMS[nearTeam]||{}).color||'#888');
        dot.setAttribute('stroke', isDark() ? '#0d1117' : '#fff');
        dot.setAttribute('stroke-width', 2);
        svgEl.appendChild(dot);
      }
    }
  }

  function onChartMouseLeave() {
    state.hovered = null;
    renderSVG();
    var tip = state.overlay.querySelector('#explorer-tooltip');
    if (tip) tip.style.display = 'none';
  }

  function showTooltip(x, y, date, teams) {
    var tip = state.overlay.querySelector('#explorer-tooltip');
    if (!date) { tip.style.display = 'none'; return; }
    var dk = isDark();
    var isComp = state.compareMode && state.compareTeams.length === 2;
    var entries;

    if (isComp) {
      entries = state.compareTeams.map(function(a) {
        return {abbrev:a, value:state.data.teams[a]&&state.data.teams[a][date], color:(TEAMS[a]||{}).color||'#888'};
      }).filter(function(e){return e.value!=null;});
    } else {
      var ordered = state.hovered ? [state.hovered] : [];
      teams.forEach(function(a) { if (a !== state.hovered) ordered.push(a); });
      entries = ordered.map(function(a) {
        return {abbrev:a, value:state.data.teams[a]&&state.data.teams[a][date], color:(TEAMS[a]||{}).color||'#888'};
      }).filter(function(e){return e.value!=null;}).sort(function(a,b) {
        if (a.abbrev === state.hovered) return -1;
        if (b.abbrev === state.hovered) return 1;
        return b.value - a.value;
      }).slice(0,12);
    }

    if (!entries.length) { tip.style.display = 'none'; return; }

    var html = '<div class="explorer-tip-date">'+fmtDate(date)+'</div>';
    entries.forEach(function(e) {
      var bold = e.abbrev === state.hovered ? ' style="font-weight:700;color:var(--text)"' : '';
      html += '<div class="explorer-tip-row">' +
        '<span class="explorer-tip-dot" style="background:'+e.color+'"></span>' +
        '<span class="explorer-tip-abbrev"'+bold+'>'+e.abbrev+'</span>' +
        '<span class="explorer-tip-val">'+e.value.toFixed(1)+'%</span></div>';
    });

    if (isComp && entries.length === 2) {
      var delta = entries[0].value - entries[1].value;
      var dColor = delta > 0 ? 'var(--green)' : delta < 0 ? 'var(--red)' : 'var(--text-muted)';
      html += '<div class="explorer-tip-delta" style="color:'+dColor+'">Gap: '+(delta>0?'+':'')+delta.toFixed(1)+'%</div>';
    }

    tip.innerHTML = html;
    tip.style.display = 'block';
    var wrapRect = state.overlay.querySelector('#explorer-chart-wrap').getBoundingClientRect();
    var tipW = tip.offsetWidth;
    tip.style.left = Math.min(x + 12, wrapRect.width - tipW - 8) + 'px';
    tip.style.top = Math.max(y - 20, 8) + 'px';
  }

  // ── Event binding ──
  function bindEvents() {
    state.overlay.querySelector('#explorer-close').addEventListener('click', close);

    state.overlay.querySelector('#explorer-filters').addEventListener('click', function(e) {
      var btn = e.target.closest('[data-filter]');
      if (btn) { applyFilter(btn.dataset.filter); return; }
      if (e.target.closest('#explorer-compare-toggle')) {
        state.compareMode = !state.compareMode;
        if (!state.compareMode) state.compareTeams = [];
        renderFilters();
        renderChips();
        renderCompareBanner();
        renderSVG();
      }
    });

    state.overlay.querySelector('#explorer-chips').addEventListener('click', function(e) {
      var chip = e.target.closest('[data-team]');
      if (!chip) return;
      var abbrev = chip.dataset.team;
      if (state.compareMode) {
        var idx = state.compareTeams.indexOf(abbrev);
        if (idx !== -1) state.compareTeams.splice(idx, 1);
        else if (state.compareTeams.length >= 2) { state.compareTeams = [state.compareTeams[1], abbrev]; }
        else state.compareTeams.push(abbrev);
        renderFilters(); renderChips(); renderCompareBanner(); renderSVG();
        return;
      }
      var vi = state.visible.indexOf(abbrev);
      if (vi !== -1) { state.visible.splice(vi, 1); }
      else state.visible.push(abbrev);
      renderChips(); renderSVG(); renderFooter();
    });

    state.overlay.querySelector('#explorer-chips').addEventListener('mouseenter', function(e) {
      var chip = e.target.closest('[data-team]');
      if (chip) { state.hovered = chip.dataset.team; renderSVG(); }
    }, true);
    state.overlay.querySelector('#explorer-chips').addEventListener('mouseleave', function(e) {
      var chip = e.target.closest('[data-team]');
      if (chip) { state.hovered = null; renderSVG(); }
    }, true);

    state.overlay.querySelector('#explorer-chart-wrap').addEventListener('mousemove', onChartMouseMove);
    state.overlay.querySelector('#explorer-chart-wrap').addEventListener('mouseleave', onChartMouseLeave);

    bindSlider();
    window.addEventListener('resize', function() { if (state.overlay) { renderSVG(); renderSlider(); } });

    renderFilters(); renderChips(); renderMovers(); renderSlider(); renderFooter(); renderCompareBanner();
  }

  function renderCompareBanner() {
    var banner = state.overlay.querySelector('#explorer-compare-banner');
    if (!state.compareMode || state.compareTeams.length >= 2) { banner.style.display = 'none'; return; }
    banner.style.display = 'block';
    banner.textContent = 'Tap ' + (2 - state.compareTeams.length) + ' team' + (state.compareTeams.length===0?'s':'') + ' below to compare head-to-head';
  }

  // ── Slider ──
  function bindSlider() {
    var dragging = null;
    var track = state.overlay.querySelector('#explorer-slider-track');
    if (!track) return;

    function getIdx(clientX) {
      var rect = track.getBoundingClientRect();
      var pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      return Math.round(pct * (state.data.dates.length - 1));
    }

    state.overlay.querySelector('#explorer-slider-wrap').addEventListener('mousedown', function(e) {
      if (e.target.id === 'explorer-handle-start') dragging = 'start';
      else if (e.target.id === 'explorer-handle-end') dragging = 'end';
      if (dragging) e.preventDefault();
    });
    document.addEventListener('mousemove', function(e) {
      if (!dragging || !state.overlay) return;
      track = state.overlay.querySelector('#explorer-slider-track');
      if (!track) return;
      var idx = getIdx(e.clientX);
      if (dragging === 'start') {
        state.dateRange[0] = Math.max(0, Math.min(idx, state.dateRange[1] - 1));
      } else {
        state.dateRange[1] = Math.min(state.data.dates.length - 1, Math.max(idx, state.dateRange[0] + 1));
      }
      renderSlider(); renderSVG();
    });
    document.addEventListener('mouseup', function() { dragging = null; });

    state.overlay.querySelector('#explorer-slider-wrap').addEventListener('touchstart', function(e) {
      if (e.target.id === 'explorer-handle-start') dragging = 'start';
      else if (e.target.id === 'explorer-handle-end') dragging = 'end';
    }, {passive:true});
    document.addEventListener('touchmove', function(e) {
      if (!dragging || !state.overlay) return;
      track = state.overlay.querySelector('#explorer-slider-track');
      if (!track) return;
      var idx = getIdx(e.touches[0].clientX);
      if (dragging === 'start') {
        state.dateRange[0] = Math.max(0, Math.min(idx, state.dateRange[1] - 1));
      } else {
        state.dateRange[1] = Math.min(state.data.dates.length - 1, Math.max(idx, state.dateRange[0] + 1));
      }
      renderSlider(); renderSVG();
    }, {passive:true});
    document.addEventListener('touchend', function() { dragging = null; });
  }

  return { open: open, close: close };
})();
