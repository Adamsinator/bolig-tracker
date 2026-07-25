'use strict';
/* Realkreditrenter subpage — self-contained. Renders the effective mortgage rate
   by rate-fixation (Nationalbanken via statbank, data/mortgage.json): current-rate
   tiles, a rate table with month-over-month change, a yield curve, a rate-history
   chart, and an annuity payment calculator. Bondstats.dk-inspired. */
const $ = (s, r = document) => r.querySelector(s);
const el = (t, a = {}, ...kids) => {
  const n = document.createElement(t);
  for (const k in a) { if (k === 'class') n.className = a[k]; else if (k === 'html') n.innerHTML = a[k]; else n.setAttribute(k, a[k]); }
  for (const c of kids) if (c != null) n.append(c.nodeType ? c : document.createTextNode(c));
  return n;
};
const SVGNS = 'http://www.w3.org/2000/svg';
const svel = (t, a = {}) => { const n = document.createElementNS(SVGNS, t); for (const k in a) n.setAttribute(k, a[k]); return n; };
const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const MONTHS_DA = ['jan', 'feb', 'mar', 'apr', 'maj', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec'];
const fmtYM = ym => { const [y, m] = String(ym).split('M'); return (MONTHS_DA[+m - 1] || '') + ' ' + y; };
const pct = v => v == null ? '–' : v.toLocaleString('da-DK', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' %';
const kr = v => v == null ? '–' : Math.round(v).toLocaleString('da-DK') + ' kr.';
const COLORS = ['#e6212a', '#e08a00', '#12a06f', '#1c5cb0', '#7a5cff', '#00a3c7', '#555'];

// theme (shared key with the main app)
(function () {
  const saved = localStorage.getItem('hbTheme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
  addEventListener('DOMContentLoaded', () => {
    const tt = $('#themeToggle'); if (!tt) return;
    tt.addEventListener('click', () => {
      const cur = document.documentElement.getAttribute('data-theme');
      const dark = cur ? cur === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
      document.documentElement.setAttribute('data-theme', dark ? 'light' : 'dark');
      localStorage.setItem('hbTheme', dark ? 'light' : 'dark');
      if (MO) renderAll(MO);
    });
  });
})();

// tiny tooltip
const TT = () => $('#tooltip');
function showTip(html, x, y) { const t = TT(); t.innerHTML = html; t.hidden = false; const w = t.offsetWidth, h = t.offsetHeight; t.style.left = Math.min(x + 14, innerWidth - w - 8) + 'px'; t.style.top = Math.max(8, y - h - 12) + 'px'; }
const hideTip = () => { TT().hidden = true; };

let MO = null;

async function boot() {
  try {
    MO = await fetch('data/mortgage.json').then(r => r.json());
  } catch (e) { $('#rateTable').append(el('div', { class: 'loading' }, 'Kunne ikke hente renter.')); return; }
  renderAll(MO);
}

function renderAll(mo) {
  const order = (mo.order || Object.keys(mo.series)).filter(l => mo.series[l]);
  const asof = mo.latest['Alle lån'] ? fmtYM(mo.latest['Alle lån'].month) : '';
  $('#asof').textContent = asof ? 'Pr. ' + asof : '';
  $('#tableSrc').textContent = asof ? '· pr. ' + asof : '';
  $('#bondNote').textContent = `${mo.unit}. Kilde: ${mo.source}. Renten er den gennemsnitlige effektive rente på nyudstedte realkreditlån efter oprindelig rentebinding — ikke et konkret kurstilbud. Kurser på de enkelte obligationsserier (fx 30-årig 4 %) kræver en børsdatakilde og kommer i en senere version.`;

  renderTiles(mo, order);
  renderTable(mo, order);
  renderCurve(mo);
  renderHist(mo, order);
  renderCalc(mo, order);
}

// headline tiles: the four most-quoted buckets
function renderTiles(mo, order) {
  const box = $('#rateTiles'); box.innerHTML = ''; box.removeAttribute('aria-busy');
  const want = ['Variabel (≤3 mdr.)', '1–5 år', 'Fast (>10 år)', 'Alle lån'];
  const labs = order.filter(l => want.includes(l));
  (labs.length ? labs : order.slice(0, 4)).forEach(lab => {
    const l = mo.latest[lab]; if (!l) return;
    box.append(el('div', { class: 'kpi' },
      el('div', { class: 'k-label' }, lab),
      el('div', { class: 'k-val' }, pct(l.rate)),
      el('div', { class: 'k-sub' }, 'effektiv rente inkl. bidrag')));
  });
}

// full table with month-over-month change
function renderTable(mo, order) {
  const wrap = $('#rateTable'); wrap.innerHTML = '';
  const t = el('table', { class: 'rate-table' });
  t.append(el('thead', {}, el('tr', {}, el('th', {}, 'Rentebinding'), el('th', {}, 'Effektiv rente'), el('th', {}, 'Ænd. md.'), el('th', {}, 'Opdateret'))));
  const tb = el('tbody');
  order.forEach(lab => {
    const s = mo.series[lab], l = mo.latest[lab]; if (!l) return;
    // month-over-month change = latest minus previous non-null
    let prev = null;
    for (let i = s.length - 1; i >= 0; i--) { if (s[i] != null) { if (prev === 'seen') { prev = s[i]; break; } if (s[i] === l.rate) prev = 'seen'; } }
    const chg = (typeof prev === 'number') ? Math.round((l.rate - prev) * 100) / 100 : null;
    const chgCell = chg == null ? el('td', { class: 'muted' }, '–')
      : el('td', { class: chg > 0 ? 'up' : chg < 0 ? 'down' : 'muted' }, (chg > 0 ? '+' : '') + chg.toLocaleString('da-DK', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
    tb.append(el('tr', {}, el('td', {}, lab), el('td', { class: 'num strong' }, pct(l.rate)), chgCell, el('td', { class: 'muted' }, fmtYM(l.month))));
  });
  t.append(tb); wrap.append(t);
}

// yield curve — effective rate across the term-ordered fixation buckets
function renderCurve(mo) {
  const mount = $('#curveChart'); mount.innerHTML = '';
  const order = ['Variabel (≤3 mdr.)', 'Variabel (≤6 mdr.)', 'Kort rente (≤1 år)', '1–5 år', '5–10 år', 'Fast (>10 år)'];
  const pts = order.filter(l => mo.latest[l]).map(l => ({ lab: l.replace(/ \(.*\)/, '').replace('Variabel', 'Var.'), v: mo.latest[l].rate, full: l }));
  if (pts.length < 2) { mount.append(el('div', { class: 'loading' }, 'Ingen data.')); return; }
  const W = 640, H = 300, padL = 42, padR = 14, padT = 16, padB = 74, plotW = W - padL - padR, plotH = H - padT - padB;
  const vals = pts.map(p => p.v), lo = Math.max(0, Math.min(...vals) - 0.6), hi = Math.max(...vals) + 0.4;
  const X = i => padL + (pts.length === 1 ? plotW / 2 : i / (pts.length - 1) * plotW);
  const Y = v => padT + plotH - (v - lo) / (hi - lo) * plotH;
  const svg = svel('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' });
  for (let g = 0; g <= 4; g++) { const yv = lo + (hi - lo) * g / 4, y = Y(yv); svg.append(svel('line', { x1: padL, y1: y, x2: W - padR, y2: y, class: 'gridline' })); const tx = svel('text', { x: padL - 6, y: y + 3, 'text-anchor': 'end', class: 'axis-txt' }); tx.textContent = yv.toFixed(1) + '%'; svg.append(tx); }
  let d = '';
  pts.forEach((p, i) => { d += (i ? ' L' : 'M') + X(i).toFixed(1) + ' ' + Y(p.v).toFixed(1); });
  svg.append(svel('path', { d, fill: 'none', stroke: cssVar('--condo') || '#1c5cb0', 'stroke-width': 2.6, 'stroke-linejoin': 'round' }));
  pts.forEach((p, i) => {
    const g = svel('g');
    g.append(svel('circle', { cx: X(i), cy: Y(p.v), r: 4, fill: cssVar('--condo') || '#1c5cb0' }));
    const vt = svel('text', { x: X(i), y: Y(p.v) - 8, 'text-anchor': 'middle', class: 'bar-val' }); vt.textContent = p.v.toFixed(2); g.append(vt);
    const lx = X(i), ly = H - padB + 16; const lt = svel('text', { x: lx, y: ly, class: 'axis-txt', 'text-anchor': 'end', transform: `rotate(-35 ${lx} ${ly})` }); lt.textContent = p.lab; g.append(lt);
    g.addEventListener('mousemove', e => showTip(`<div class="tt-title">${p.full}</div><div class="tt-row"><span>Effektiv rente</span><b>${pct(p.v)}</b></div>`, e.clientX, e.clientY));
    g.addEventListener('mouseleave', hideTip);
    svg.append(g);
  });
  mount.append(svg);
}

// rate history — key fixations over the last ~12 years
function renderHist(mo, order) {
  const mount = $('#histChart'); mount.innerHTML = '';
  const months = mo.months, n = months.length, start = Math.max(0, n - 144);
  const xs = months.slice(start);
  const want = ['Variabel (≤3 mdr.)', '1–5 år', 'Fast (>10 år)', 'Alle lån'];
  const labs = order.filter(l => want.includes(l));
  const series = (labs.length ? labs : order).map((lab, i) => ({ name: lab, color: COLORS[i % COLORS.length], values: mo.series[lab].slice(start) }));
  lineChart(mount, xs.map(fmtYM), series);
}

// annuity payment calculator
function renderCalc(mo, order) {
  const build = () => {
    const amt = Math.max(0, +$('#calcAmount').value || 0);
    const yrs = +$('#calcTerm').value || 30;
    const nMon = yrs * 12;
    const wrap = $('#calcTable'); wrap.innerHTML = '';
    const t = el('table', { class: 'rate-table' });
    t.append(el('thead', {}, el('tr', {}, el('th', {}, 'Rentebinding'), el('th', {}, 'Rente'), el('th', {}, 'Md. ydelse'), el('th', {}, 'Samlet tilbagebetaling'))));
    const tb = el('tbody');
    order.forEach(lab => {
      const l = mo.latest[lab]; if (!l) return;
      const r = l.rate / 100 / 12;
      const m = r > 0 ? amt * r / (1 - Math.pow(1 + r, -nMon)) : amt / nMon;
      tb.append(el('tr', {}, el('td', {}, lab), el('td', { class: 'num' }, pct(l.rate)), el('td', { class: 'num strong' }, kr(m)), el('td', { class: 'num muted' }, kr(m * nMon))));
    });
    t.append(tb); wrap.append(t);
  };
  $('#calcAmount').oninput = build;
  $('#calcTerm').onchange = build;
  build();
}

// compact multi-series line chart with crosshair tooltip
function lineChart(mount, xLabels, series) {
  mount.innerHTML = '';
  const pts = xLabels.length;
  if (!pts || !series.some(s => s.values.some(v => v != null))) { mount.append(el('div', { class: 'loading' }, 'Ingen data.')); return; }
  const W = 640, H = 280, padL = 40, padR = 14, padT = 12, padB = 30, plotW = W - padL - padR, plotH = H - padT - padB;
  const all = series.flatMap(s => s.values).filter(v => v != null);
  let lo = Math.min(...all), hi = Math.max(...all); const span = (hi - lo) || 1; lo -= span * .08; hi += span * .08; lo = Math.max(0, lo);
  const X = i => padL + (pts === 1 ? plotW / 2 : i / (pts - 1) * plotW);
  const Y = v => padT + plotH - (v - lo) / (hi - lo) * plotH;
  const svg = svel('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' });
  for (let g = 0; g <= 4; g++) { const yv = lo + (hi - lo) * g / 4, y = Y(yv); svg.append(svel('line', { x1: padL, y1: y, x2: W - padR, y2: y, class: 'gridline' })); const tx = svel('text', { x: padL - 6, y: y + 3, 'text-anchor': 'end', class: 'axis-txt' }); tx.textContent = yv.toFixed(1) + '%'; svg.append(tx); }
  const N = pts, step = Math.max(1, Math.ceil((N - 1) / 6));
  for (let i = 0; i < N; i += step) { const x = X(i); const tx = svel('text', { x, y: H - padB + 15, 'text-anchor': 'middle', class: 'axis-txt' }); tx.textContent = xLabels[i]; svg.append(tx); }
  series.forEach(s => {
    let d = '', started = false;
    s.values.forEach((v, i) => { if (v == null) { started = false; return; } d += (started ? ' L' : ' M') + X(i).toFixed(1) + ' ' + Y(v).toFixed(1); started = true; });
    svg.append(svel('path', { d: d.trim(), fill: 'none', stroke: s.color, 'stroke-width': 2.2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
  });
  const cross = svel('line', { y1: padT, y2: padT + plotH, class: 'crosshair', 'stroke-dasharray': '3 3' }); cross.style.display = 'none'; svg.append(cross);
  const hit = svel('rect', { x: padL, y: padT, width: plotW, height: plotH, fill: 'transparent' });
  hit.addEventListener('mousemove', e => {
    const r = svg.getBoundingClientRect(); const px = (e.clientX - r.left) / r.width * W;
    let i = Math.round((px - padL) / plotW * (pts - 1)); i = Math.max(0, Math.min(pts - 1, i));
    cross.setAttribute('x1', X(i)); cross.setAttribute('x2', X(i)); cross.style.display = '';
    const rows = series.map(s => s.values[i] != null ? `<div class="tt-row"><span><i class="dot" style="background:${s.color}"></i>${s.name}</span><b>${pct(s.values[i])}</b></div>` : '').join('');
    showTip(`<div class="tt-title">${xLabels[i]}</div>${rows}`, e.clientX, e.clientY);
  });
  hit.addEventListener('mouseleave', () => { cross.style.display = 'none'; hideTip(); });
  svg.append(hit); mount.append(svg);
  const lg = el('div', { class: 'chart-legend' });
  series.forEach(s => lg.append(el('span', { class: 'legend-item' }, el('span', { class: 'swatch', style: `background:${s.color}` }), s.name)));
  mount.append(lg);
}

boot();
