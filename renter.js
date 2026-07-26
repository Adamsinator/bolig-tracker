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
// grouped-number inputs: read digits only, keep the field showing 600.000 etc.
const numVal = id => { const e = $(id); return e ? (+String(e.value).replace(/\D/g, '') || 0) : 0; };
function wireNumInput(id, onChange) {
  const e = $(id); if (!e) return;
  const fmt = () => { const n = String(e.value).replace(/\D/g, ''); e.value = n ? (+n).toLocaleString('da-DK') : ''; };
  fmt();  // format the initial value
  e.addEventListener('input', () => { fmt(); onChange(); });
}
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
  $('#asof').textContent = asof ? 'Pr. ' + asof + ' · månedstal' : '';
  $('#tableSrc').textContent = asof ? '· pr. ' + asof : '';
  $('#bondNote').textContent = `${mo.unit}. Kilde: ${mo.source}. Tallene er månedstal — Danmarks Statistik/Nationalbanken opgør renterne én gang om måneden, så nyeste punkt er sidst opgjorte måned. Renten er den gennemsnitlige effektive rente på nyudstedte realkreditlån efter oprindelig rentebinding — ikke et konkret kurstilbud. »Fast (>10 år)« dækker lån med renten bundet i over 10 år og er reelt det 30-årige fastforrentede realkreditlån — det almindelige fastrenteprodukt. »Alle lån« er det vægtede gennemsnit på tværs af alle rentebindinger. Renterne er opgjort på tværs af alle institutter; den 30-årige obligationskurs er stort set ens hos Totalkredit, Realkredit Danmark, Nordea Kredit og Jyske Realkredit — det er primært bidragssatsen, der adskiller dem. Konkrete kurser pr. obligationsserie kræver en børsdatakilde.`;

  renderTiles(mo, order);
  renderTable(mo, order);
  renderCurve(mo);
  renderHist(mo, order);
  renderAfford(mo, order);
  renderCalc(mo, order);

  const rng = $('#histRange');
  if (rng && !rng.dataset.wired) {
    rng.dataset.wired = '1';
    rng.addEventListener('click', e => {
      const b = e.target.closest('button'); if (!b) return;
      HIST_MONTHS = +b.dataset.m || 144;
      [...rng.children].forEach(c => c.classList.toggle('active', c === b));
      renderHist(mo, order);
    });
  }
}

// Danish home-financing rules for the affordability calculator
const MIN_DOWN = 0.05;      // min. 5 % udbetaling
const MAX_LTV = 0.80;       // realkredit maks. 80 % af købsprisen
const DEBT_MULT = 5;        // samlet gæld højst ~5× årlig husstandsindkomst
const BANK_MARGIN = 2.0;    // banklån koster typisk ~2 pct.point mere end realkredit

function renderAfford(mo, order) {
  const fix = $('#affFix');
  if (fix && !fix.options.length) {
    order.filter(l => l !== 'Alle lån').forEach(l => {
      const o = document.createElement('option'); o.value = l; o.textContent = l;
      if (l === 'Fast (>10 år)') o.selected = true; fix.append(o);
    });
  }
  const build = () => {
    const monthlyIncome = Math.max(0, numVal('#affIncome'));
    const income = monthlyIncome * 12;                        // regel: gæld ≤ 5× ÅRLIG indkomst
    const savings = Math.max(0, numVal('#affSavings'));
    const fixLab = $('#affFix').value || 'Fast (>10 år)';
    const yrs = +$('#affTerm').value || 30;
    const rk = (mo.latest[fixLab] || mo.latest['Fast (>10 år)'] || { rate: 5 }).rate;
    const bankRate = rk + BANK_MARGIN;
    const maxByDown = savings > 0 ? savings / MIN_DOWN : 0;   // udbetaling ≥ 5 % af prisen
    const maxByIncome = DEBT_MULT * income + savings;         // samlet gæld ≤ 5× årlig indkomst
    const P = Math.max(0, Math.min(maxByDown, maxByIncome));
    const bind = maxByDown <= maxByIncome ? 'udbetaling' : 'indkomst';
    const down = Math.min(savings, P);
    const loan = Math.max(0, P - down);
    const rkLoan = Math.min(MAX_LTV * P, loan);
    const bankLoan = Math.max(0, loan - rkLoan);
    const ann = (amt, rate) => { const r = rate / 100 / 12, n = yrs * 12; return r > 0 ? amt * r / (1 - Math.pow(1 + r, -n)) : (n ? amt / n : 0); };
    const mRk = ann(rkLoan, rk), mBank = ann(bankLoan, bankRate), mTot = mRk + mBank;

    const tile = (v, l) => el('div', { class: 'kstat' }, el('div', { class: 'kstat-v' }, v), el('div', { class: 'kstat-l' }, l));
    const head = $('#affHead'); head.innerHTML = '';
    head.append(
      tile(kr(P), 'Maks. købspris'),
      tile(kr(down) + (P ? ' · ' + Math.round(down / P * 100) + ' %' : ''), 'Udbetaling'),
      tile(kr(mTot) + ' /md.', 'Samlet ydelse'));

    const wrap = $('#affTable'); wrap.innerHTML = '';
    const t = el('table', { class: 'rate-table' });
    t.append(el('thead', {}, el('tr', {}, el('th', {}, 'Finansiering'), el('th', {}, 'Beløb'), el('th', {}, 'Rente'), el('th', {}, 'Md. ydelse'))));
    const tb = el('tbody');
    tb.append(el('tr', {}, el('td', {}, 'Realkreditlån (op til 80 %)'), el('td', { class: 'num' }, kr(rkLoan)), el('td', { class: 'num' }, pct(rk)), el('td', { class: 'num strong' }, kr(mRk))));
    if (bankLoan > 0) tb.append(el('tr', {}, el('td', {}, 'Banklån (rest op til 95 %)'), el('td', { class: 'num' }, kr(bankLoan)), el('td', { class: 'num' }, pct(bankRate)), el('td', { class: 'num strong' }, kr(mBank))));
    tb.append(el('tr', {}, el('td', {}, 'Udbetaling'), el('td', { class: 'num' }, kr(down)), el('td', { class: 'muted' }, '–'), el('td', { class: 'muted' }, '–')));
    tb.append(el('tr', {}, el('td', { class: 'strong' }, 'Købspris i alt'), el('td', { class: 'num strong' }, kr(P)), el('td', { class: 'muted' }, ''), el('td', { class: 'num strong' }, kr(mTot))));
    t.append(tb); wrap.append(t);

    const note = $('#affNote');
    if (note) note.textContent = `Bindende grænse: ${bind === 'udbetaling' ? 'din udbetaling (min. 5 % af prisen)' : 'din indkomst (samlet gæld højst ' + DEBT_MULT + '× årlig husstandsindkomst)'}. Beregnet efter: min. 5 % udbetaling, realkredit maks. 80 % af prisen, resten som banklån (~${BANK_MARGIN.toLocaleString('da-DK')} pct.point dyrere end realkreditrenten). Vejledende — banken laver altid en konkret kreditvurdering med stresstest og rådighedsbeløb.`;
  };
  ['#affIncome', '#affSavings'].forEach(s => wireNumInput(s, build));
  ['#affFix', '#affTerm'].forEach(s => { const e = $(s); if (e) e.onchange = build; });
  build();
}

// headline tiles: the four most-quoted buckets
function renderTiles(mo, order) {
  const box = $('#rateTiles'); box.innerHTML = ''; box.removeAttribute('aria-busy');
  const want = ['Variabel (≤3 mdr.)', '1–5 år', 'Fast (>10 år)', 'Alle lån'];
  const labs = order.filter(l => want.includes(l));
  const subFor = lab => lab === 'Alle lån' ? 'vægtet gennemsnit — alle nye lån'
    : lab === 'Fast (>10 år)' ? '30-årig fast rente' : 'effektiv rente inkl. bidrag';
  (labs.length ? labs : order.slice(0, 4)).forEach(lab => {
    const l = mo.latest[lab]; if (!l) return;
    box.append(el('div', { class: 'kpi' },
      el('div', { class: 'k-label' }, lab === 'Fast (>10 år)' ? 'Fast (30 år)' : lab),
      el('div', { class: 'k-val' }, pct(l.rate)),
      el('div', { class: 'k-sub' }, subFor(lab))));
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
    const hint = lab === 'Alle lån' ? ' · vægtet gns.' : lab === 'Fast (>10 år)' ? ' · typisk 30-årig fast' : '';
    const labCell = hint ? el('td', {}, lab, el('span', { class: 'th-hint' }, hint)) : el('td', {}, lab);
    tb.append(el('tr', { class: lab === 'Alle lån' ? 'row-avg' : '' }, labCell, el('td', { class: 'num strong' }, pct(l.rate)), chgCell, el('td', { class: 'muted' }, fmtYM(l.month))));
  });
  t.append(tb); wrap.append(t);
}

// yield curve — effective rate across the term-ordered fixation buckets
function renderCurve(mo) {
  const mount = $('#curveChart'); mount.innerHTML = '';
  const SHORT = { 'Variabel (≤3 mdr.)': '≤3 mdr.', 'Variabel (≤6 mdr.)': '≤6 mdr.', 'Kort rente (≤1 år)': '≤1 år', '1–5 år': '1–5 år', '5–10 år': '5–10 år', 'Fast (>10 år)': 'Fast' };
  const order = ['Variabel (≤3 mdr.)', 'Variabel (≤6 mdr.)', 'Kort rente (≤1 år)', '1–5 år', '5–10 år', 'Fast (>10 år)'];
  const pts = order.filter(l => mo.latest[l]).map(l => ({ lab: SHORT[l] || l, v: mo.latest[l].rate, full: l }));
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

// linearly fill interior gaps up to maxGap months long, so a bucket that DST
// skips for a month or two (e.g. ≤3 mdr.) stays a continuous line; longer gaps
// and leading/trailing nulls stay broken (we don't invent years of history)
function fillShortGaps(vals, maxGap) {
  const out = vals.slice();
  let i = 0;
  while (i < out.length) {
    if (out[i] == null) { i++; continue; }
    let j = i + 1;
    while (j < out.length && out[j] == null) j++;
    const gap = j - i - 1;
    if (j < out.length && gap > 0 && gap <= maxGap) {
      for (let k = i + 1; k < j; k++) out[k] = out[i] + (out[j] - out[i]) * (k - i) / (j - i);
    }
    i = j;
  }
  return out;
}

// rate history — key fixations, with a selectable look-back window
let HIST_MONTHS = 144;   // default ~12 years
function renderHist(mo, order) {
  const mount = $('#histChart'); mount.innerHTML = '';
  const months = mo.months, n = months.length;
  const start = HIST_MONTHS >= n ? 0 : Math.max(0, n - HIST_MONTHS);
  const xs = months.slice(start);
  const want = ['Variabel (≤3 mdr.)', '1–5 år', 'Fast (>10 år)', 'Alle lån'];
  const labs = order.filter(l => want.includes(l));
  const series = (labs.length ? labs : order).map((lab, i) => ({ name: lab, color: COLORS[i % COLORS.length], values: fillShortGaps(mo.series[lab], 3).slice(start) }));
  lineChart(mount, xs.map(fmtYM), series);
}

// month-by-month amortisation of an annuity loan: for each month the payment
// splits into interest (on the outstanding balance) and principal (the rest).
// Interest shrinks and principal grows over the term — we return the yearly
// totals plus the month where principal first exceeds interest.
function amortise(amt, ratePctYr, nMon) {
  const r = ratePctYr / 100 / 12;
  const pay = r > 0 ? amt * r / (1 - Math.pow(1 + r, -nMon)) : amt / nMon;
  let bal = amt, cross = null;
  const years = [];
  let yInt = 0, yPri = 0;
  for (let m = 1; m <= nMon; m++) {
    const interest = bal * r;
    const principal = Math.min(pay - interest, bal);
    bal -= principal;
    yInt += interest; yPri += principal;
    if (cross == null && principal > interest) cross = m;
    if (m % 12 === 0 || m === nMon) { years.push({ interest: yInt, principal: yPri, bal: Math.max(0, bal) }); yInt = 0; yPri = 0; }
  }
  return { pay, years, cross, totalInterest: years.reduce((a, y) => a + y.interest, 0) };
}

// inverse of the annuity formula: the loan a given monthly payment can service
function loanFromPayment(pay, ratePctYr, nMon) {
  const r = ratePctYr / 100 / 12;
  return r > 0 ? pay * (1 - Math.pow(1 + r, -nMon)) / r : pay * nMon;
}
// parse a Danish-typed rate ("4,25" or "4.25") into a number
const parseRate = id => { const e = $(id); return e ? (parseFloat(String(e.value).replace(',', '.')) || 0) : 0; };

// stacked bar chart: afdrag (bottom) + rente (top) per year, with the crossover
// year marked — shows how an annuity shifts from mostly-interest to mostly-principal
function amortChart(mount, years, crossYear) {
  mount.innerHTML = '';
  if (!years.length) { mount.append(el('div', { class: 'loading' }, 'Ingen data.')); return; }
  const W = 640, H = 260, padL = 48, padR = 12, padT = 12, padB = 34, plotW = W - padL - padR, plotH = H - padT - padB;
  const max = Math.max(...years.map(y => y.interest + y.principal)) || 1;
  const n = years.length, bw = plotW / n * 0.72, gap = plotW / n;
  const Y = v => padT + plotH - v / max * plotH;
  const svg = svel('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' });
  for (let g = 0; g <= 4; g++) { const yv = max * g / 4, y = Y(yv); svg.append(svel('line', { x1: padL, y1: y, x2: W - padR, y2: y, class: 'gridline' })); const tx = svel('text', { x: padL - 6, y: y + 3, 'text-anchor': 'end', class: 'axis-txt' }); tx.textContent = Math.round(yv / 1000) + 'k'; svg.append(tx); }
  const cAf = cssVar('--good') || '#1a7f37', cRe = cssVar('--bad') || '#c0392b';
  years.forEach((y, i) => {
    const x = padL + i * gap + (gap - bw) / 2;
    const hAf = y.principal / max * plotH, hRe = y.interest / max * plotH;
    const g = svel('g');
    g.append(svel('rect', { x, y: Y(y.principal), width: bw, height: hAf, fill: cAf, opacity: .85 }));
    g.append(svel('rect', { x, y: Y(y.principal + y.interest), width: bw, height: hRe, fill: cRe, opacity: .8 }));
    g.addEventListener('mousemove', e => showTip(`<div class="tt-title">År ${i + 1}</div><div class="tt-row"><span><i class="dot" style="background:${cAf}"></i>Afdrag</span><b>${kr(y.principal)}</b></div><div class="tt-row"><span><i class="dot" style="background:${cRe}"></i>Rente</span><b>${kr(y.interest)}</b></div>`, e.clientX, e.clientY));
    g.addEventListener('mouseleave', hideTip);
    svg.append(g);
  });
  const step = Math.max(1, Math.ceil(n / 10));
  for (let i = 0; i < n; i += step) { const x = padL + i * gap + gap / 2; const tx = svel('text', { x, y: H - padB + 15, 'text-anchor': 'middle', class: 'axis-txt' }); tx.textContent = (i + 1); svg.append(tx); }
  if (crossYear && crossYear <= n) {
    const x = padL + (crossYear - 1) * gap + gap / 2;
    svg.append(svel('line', { x1: x, y1: padT, x2: x, y2: padT + plotH, class: 'crosshair', 'stroke-dasharray': '3 3' }));
  }
  mount.append(svg);
  const lg = el('div', { class: 'chart-legend' });
  lg.append(el('span', { class: 'legend-item' }, el('span', { class: 'swatch', style: `background:${cAf}` }), 'Afdrag'),
    el('span', { class: 'legend-item' }, el('span', { class: 'swatch', style: `background:${cRe}` }), 'Rente'));
  mount.append(lg);
}

// full year-by-year amortisation schedule (afdragsplan)
function scheduleTable(mount, am, yrs) {
  mount.innerHTML = '';
  const t = el('table', { class: 'rate-table afdrag' });
  t.append(el('thead', {}, el('tr', {}, el('th', {}, 'År'), el('th', {}, 'Ydelse'), el('th', {}, 'Rente'), el('th', {}, 'Afdrag'), el('th', {}, 'Restgæld'))));
  const tb = el('tbody');
  am.years.forEach((y, i) => {
    tb.append(el('tr', {}, el('td', {}, i + 1), el('td', { class: 'num' }, kr(y.interest + y.principal)),
      el('td', { class: 'num' }, kr(y.interest)), el('td', { class: 'num' }, kr(y.principal)), el('td', { class: 'num strong' }, kr(y.bal))));
  });
  t.append(tb); mount.append(t);
}

// annuity payment calculator — amount- or payment-driven, with a custom-rate
// option, an amortisation chart, a full afdragsplan and a per-fixation table
function renderCalc(mo, order) {
  const fix = $('#calcFix');
  if (fix && !fix.options.length) {
    order.forEach(l => { const o = document.createElement('option'); o.value = l; o.textContent = l; if (l === 'Fast (>10 år)') o.selected = true; fix.append(o); });
    const oc = document.createElement('option'); oc.value = '__custom'; oc.textContent = 'Egen rente…'; fix.append(oc);
  }
  const tile = (v, l) => el('div', { class: 'kstat' }, el('div', { class: 'kstat-v' }, v), el('div', { class: 'kstat-l' }, l));
  let mode = 'amount';
  const build = () => {
    const yrs = +$('#calcTerm').value || 30;
    const nMon = yrs * 12;
    const fixLab = ($('#calcFix') && $('#calcFix').value) || 'Fast (>10 år)';
    const custom = fixLab === '__custom';
    $('#calcRateWrap').hidden = !custom;
    const rate = custom ? parseRate('#calcRate') : (mo.latest[fixLab] || mo.latest['Fast (>10 år)'] || { rate: 5 }).rate;
    const rateLbl = custom ? 'egen rente' : fixLab.toLowerCase();

    // resolve the loan amount — either typed directly or implied by a target ydelse
    let amt;
    if (mode === 'payment') { amt = Math.round(loanFromPayment(Math.max(0, numVal('#calcPay')), rate, nMon)); }
    else { amt = Math.max(0, numVal('#calcAmount')); }
    const am = amortise(amt, rate, nMon);
    const firstInt = amt * (rate / 100 / 12), firstPri = am.pay - firstInt;

    const head = $('#calcSplit'); if (head) {
      head.innerHTML = '';
      if (mode === 'payment') head.append(tile(kr(amt), 'Lånebeløb (beregnet)'), tile(kr(am.pay) + ' /md.', 'Månedlig ydelse'), tile(kr(am.totalInterest), 'Samlet rente'));
      else head.append(tile(kr(am.pay) + ' /md.', 'Månedlig ydelse'), tile(kr(firstInt) + ' /md.', 'Heraf rente (1. md.)'), tile(kr(firstPri) + ' /md.', 'Heraf afdrag (1. md.)'));
    }
    amortChart($('#calcAmort'), am.years, am.cross ? Math.ceil(am.cross / 12) : null);
    const note = $('#calcAmortNote');
    if (note) {
      const crossYr = am.cross ? (am.cross / 12) : null;
      note.textContent = crossYr
        ? `Ved ${rateLbl} (${pct(rate)}) går der ca. ${crossYr < 1 ? 'under 1 år' : Math.round(crossYr) + ' år'}, før du betaler mere i afdrag end i rente. Samlet rente over ${yrs} år: ${kr(am.totalInterest)}`
        : `Ved ${rateLbl} (${pct(rate)}) betaler du fra første måned mere i afdrag end i rente. Samlet rente over ${yrs} år: ${kr(am.totalInterest)}`;
    }
    scheduleTable($('#calcSchedule'), am, yrs);

    const wrap = $('#calcTable'); wrap.innerHTML = '';
    const t = el('table', { class: 'rate-table' });
    t.append(el('thead', {}, el('tr', {}, el('th', {}, 'Rentebinding'), el('th', {}, 'Rente'), el('th', {}, 'Md. ydelse'), el('th', {}, 'Samlet rente'), el('th', {}, 'Samlet tilbagebetaling'))));
    const tb = el('tbody');
    order.forEach(lab => {
      const l = mo.latest[lab]; if (!l) return;
      const a = amortise(amt, l.rate, nMon);
      tb.append(el('tr', { class: (!custom && lab === fixLab) ? 'row-avg' : '' }, el('td', {}, lab), el('td', { class: 'num' }, pct(l.rate)), el('td', { class: 'num strong' }, kr(a.pay)), el('td', { class: 'num muted' }, kr(a.totalInterest)), el('td', { class: 'num muted' }, kr(a.pay * nMon))));
    });
    t.append(tb); wrap.append(t);
  };

  const modeSeg = $('#calcMode');
  if (modeSeg && !modeSeg.dataset.wired) {
    modeSeg.dataset.wired = '1';
    modeSeg.addEventListener('click', e => {
      const b = e.target.closest('button'); if (!b) return;
      mode = b.dataset.mode;
      [...modeSeg.children].forEach(c => c.classList.toggle('active', c === b));
      $('#calcAmountWrap').hidden = mode !== 'amount';
      $('#calcPayWrap').hidden = mode !== 'payment';
      build();
    });
  }
  wireNumInput('#calcAmount', build);
  wireNumInput('#calcPay', build);
  $('#calcTerm').onchange = build;
  if ($('#calcFix')) $('#calcFix').onchange = build;
  const cr = $('#calcRate'); if (cr) cr.oninput = build;
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
