/* Bolig Tracker — model.html: diagnostics for the hedonic fair-value model.
   The model itself lives in model.js (shared with the listings page); this file
   only measures and draws it. */
const $ = (s, r = document) => r.querySelector(s);
const SVGNS = 'http://www.w3.org/2000/svg';
const svel = (t, a = {}) => { const n = document.createElementNS(SVGNS, t); for (const k in a) n.setAttribute(k, a[k]); return n; };
const el = (t, a = {}, ...kids) => {
  const n = document.createElement(t);
  for (const k in a) { if (k === 'class') n.className = a[k]; else if (k === 'html') n.innerHTML = a[k]; else n.setAttribute(k, a[k]); }
  kids.forEach(k => k != null && n.append(k));
  return n;
};
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const nf = n => Math.round(n).toLocaleString('da-DK');
const m2 = v => nf(v) + ' kr/m²';
const med = a => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

/* theme toggle — same behaviour as the other pages */
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
      render();   // charts read CSS colours, so repaint on theme change
    });
  });
})();

let DATA = null;

async function boot() {
  try {
    const [listings, sold, meta] = await Promise.all([
      fetch('data/listings.json').then(r => r.json()),
      fetch('data/sold.json').then(r => r.json()).catch(() => null),
      fetch('data/meta.json').then(r => r.json()).catch(() => null),
    ]);
    DATA = { listings, sold, meta };
    if (meta && meta.generatedAt) {
      $('#asof').textContent = 'Opdateret ' + new Date(meta.generatedAt)
        .toLocaleDateString('da-DK', { day: 'numeric', month: 'short', year: 'numeric' });
    }
    render();
  } catch (e) {
    $('#mdlTiles').innerHTML = '<div class="loading">Kunne ikke hente data.</div>';
  }
}

function render() {
  if (!DATA) return;
  const fv = BT.fairValue(DATA.listings, DATA.sold);
  const rows = DATA.listings.filter(r => fv.pred.has(r.id));
  tiles(fv, rows);
  readMe(fv);
  scatter(rows, fv);
  hist(rows, fv);
  segments(rows, fv);
  odd(fv);
}

function tiles(fv, rows) {
  const box = $('#mdlTiles'); box.innerHTML = ''; box.removeAttribute('aria-busy');
  const band = (fv.lo && fv.hi) ? `−${Math.round((1 - fv.lo) * 100)} % / +${Math.round((fv.hi - 1) * 100)} %` : '–';
  [
    { l: 'Typisk afvigelse', v: fv.mape != null ? '±' + fv.mape + ' %' : '–', s: 'uden for stikprøven' },
    { l: 'Usikkerhedsspænd', v: band, s: '10–90 % af boligerne' },
    { l: 'Forklaringsgrad (R²)', v: fv.r2 != null ? fv.r2.toFixed(2) : '–', s: 'af variationen i kr/m²' },
    { l: 'Boliger i modellen', v: nf(rows.length), s: 'udbudte boliger' },
    { l: 'Holdt udenfor', v: nf(fv.odd.size), s: 'atypiske boliger' },
  ].forEach(k => box.append(el('div', { class: 'kpi' },
    el('div', { class: 'k-label' }, k.l), el('div', { class: 'k-val' }, k.v), el('div', { class: 'k-sub' }, k.s))));
}

function readMe(fv) {
  const p = fv.mape;
  $('#readMe').innerHTML = p == null ? 'Ikke nok data til at måle modellen.' :
    `Modellen rammer typisk inden for <strong>±${p} %</strong> af udbudsprisen på en bolig, den ikke har set før. ` +
    `Halvdelen af boligerne rammes bedre end det — men en ud af ti afviger mere end spændet ovenfor. ` +
    `Derfor markeres en bolig først som “under” eller “over vurdering” på forsiden, når afvigelsen er <em>større</em> ` +
    `end modellens egen usikkerhed; ellers ville almindelig støj blive solgt som et fund.`;
}

/* model vs asking, log–log so the cheap end isn't squashed into the corner */
function scatter(rows, fv) {
  const mount = $('#scatter'); mount.innerHTML = '';
  const pts = rows.filter(r => r.m2p > 0 && fv.pred.get(r.id) > 0 && !fv.odd.has(r.id));
  if (pts.length < 20) return;
  const W = 560, H = 380, padL = 54, padR = 12, padT = 10, padB = 34;
  const xs = pts.map(r => Math.log(fv.pred.get(r.id))), ys = pts.map(r => Math.log(r.m2p));
  const lo = Math.min(...xs, ...ys), hi = Math.max(...xs, ...ys);
  const X = v => padL + (v - lo) / (hi - lo) * (W - padL - padR);
  const Y = v => H - padB - (v - lo) / (hi - lo) * (H - padT - padB);
  const svg = svel('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Model mod udbudspris' });
  for (let g = 0; g <= 4; g++) {
    const v = lo + (hi - lo) * g / 4;
    svg.append(svel('line', { x1: padL, y1: Y(v), x2: W - padR, y2: Y(v), class: 'gridline' }));
    const tx = svel('text', { x: padL - 6, y: Y(v) + 3, 'text-anchor': 'end', class: 'axis-txt' });
    tx.textContent = Math.round(Math.exp(v) / 1000) + 'k';
    svg.append(tx);
    const bx = svel('text', { x: X(v), y: H - padB + 15, 'text-anchor': 'middle', class: 'axis-txt' });
    bx.textContent = Math.round(Math.exp(v) / 1000) + 'k';
    svg.append(bx);
  }
  svg.append(svel('line', { x1: X(lo), y1: Y(lo), x2: X(hi), y2: Y(hi), stroke: cssVar('--muted'), 'stroke-width': 1.4, 'stroke-dasharray': '5 5' }));
  pts.forEach(r => {
    const c = r.t === 'villa' ? cssVar('--villa') : cssVar('--condo');
    svg.append(svel('circle', { cx: X(Math.log(fv.pred.get(r.id))), cy: Y(Math.log(r.m2p)), r: 1.7, fill: c, 'fill-opacity': .35 }));
  });
  const lx = svel('text', { x: (padL + W - padR) / 2, y: H - 4, 'text-anchor': 'middle', class: 'axis-txt' });
  lx.textContent = 'Modellens vurdering (kr/m²)';
  svg.append(lx);
  mount.append(svg);
}

/* distribution of (asking / model − 1) */
function hist(rows, fv) {
  const mount = $('#hist'); mount.innerHTML = '';
  const res = rows.filter(r => !fv.odd.has(r.id)).map(r => fv.resid.get(r.id)).filter(v => v != null);
  if (res.length < 20) return;
  const LO = -60, HI = 60, STEP = 5;
  const bins = new Array((HI - LO) / STEP).fill(0);
  res.forEach(v => { const i = Math.floor((Math.max(LO, Math.min(HI - 0.001, v)) - LO) / STEP); bins[i]++; });
  const W = 560, H = 300, padL = 44, padR = 12, padT = 10, padB = 34;
  const max = Math.max(...bins);
  const bw = (W - padL - padR) / bins.length;
  const svg = svel('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Fordeling af afvigelser' });
  for (let g = 0; g <= 4; g++) {
    const y = H - padB - (H - padT - padB) * g / 4;
    svg.append(svel('line', { x1: padL, y1: y, x2: W - padR, y2: y, class: 'gridline' }));
    const tx = svel('text', { x: padL - 6, y: y + 3, 'text-anchor': 'end', class: 'axis-txt' });
    tx.textContent = nf(max * g / 4);
    svg.append(tx);
  }
  bins.forEach((n, i) => {
    const h = max ? (H - padT - padB) * n / max : 0;
    const mid = LO + i * STEP + STEP / 2;
    svg.append(svel('rect', { x: padL + i * bw + 0.6, y: H - padB - h, width: Math.max(1, bw - 1.2), height: h,
      fill: Math.abs(mid) <= (fv.mape || 12) ? cssVar('--condo') : cssVar('--muted'), 'fill-opacity': .85, rx: 1.5 }));
    if (i % 4 === 0) {
      const tx = svel('text', { x: padL + i * bw + bw / 2, y: H - padB + 15, 'text-anchor': 'middle', class: 'axis-txt' });
      tx.textContent = (mid > 0 ? '+' : '') + Math.round(mid) + '%';
      svg.append(tx);
    }
  });
  mount.append(svg);
  const within = res.filter(v => Math.abs(v) <= (fv.mape || 12)).length;
  $('#histNote').textContent = `${Math.round(within / res.length * 100)} % af boligerne ligger inden for ±${fv.mape || 12} % af modellen (blå søjler). Halerne er de boliger, hvor udbudsprisen og modellen er markant uenige.`;
}

/* median absolute deviation per kommune and per type */
function segments(rows, fv) {
  const t = $('#segTable'); t.innerHTML = '';
  const used = rows.filter(r => !fv.odd.has(r.id) && fv.resid.get(r.id) != null);
  const muniName = {};
  (DATA.meta && DATA.meta.municipalities || []).forEach(m => { muniName[m.slug] = m.name; });
  const group = key => {
    const g = {};
    used.forEach(r => { (g[key(r)] = g[key(r)] || []).push(Math.abs(fv.resid.get(r.id))); });
    return Object.entries(g).map(([k, v]) => ({ k, n: v.length, e: med(v) }))
      .filter(x => x.n >= 20).sort((a, b) => a.e - b.e);
  };
  const rowsOut = [
    ...group(r => 'type:' + (r.t === 'villa' ? 'Villaer' : 'Ejerlejligheder')),
    ...group(r => 'muni:' + (muniName[r.muni] || r.muni)),
  ];
  const worst = Math.max(...rowsOut.map(x => x.e), 1);
  t.append(el('tr', {}, el('th', {}, 'Segment'), el('th', { class: 'num' }, 'Boliger'),
    el('th', { class: 'num' }, 'Typisk afvigelse'), el('th', {}, '')));
  rowsOut.forEach(x => {
    const [kind, name] = x.k.split(':');
    const bar = el('span', {}); bar.style.width = Math.max(4, x.e / worst * 110) + 'px';
    t.append(el('tr', {},
      el('td', {}, (kind === 'type' ? '🏘 ' : '') + name),
      el('td', { class: 'num' }, nf(x.n)),
      el('td', { class: 'num' }, '±' + Math.round(x.e) + ' %'),
      el('td', {}, el('span', { class: 'bar-cell' }, el('i', {}, bar)))));
  });
}

/* the listings the robust refit drops */
function odd(fv) {
  const t = $('#oddTable'); t.innerHTML = '';
  const list = DATA.listings.filter(r => fv.odd.has(r.id))
    .sort((a, b) => (a.m2p || 0) - (b.m2p || 0));
  $('#oddCount').textContent = '· ' + list.length + ' boliger';
  t.append(el('tr', {}, el('th', {}, 'Adresse'), el('th', {}, 'Type'),
    el('th', { class: 'num' }, 'kr/m²'), el('th', { class: 'num' }, 'Afvigelse'), el('th', {}, 'Bemærkning')));
  list.slice(0, 60).forEach(r => {
    const d = fv.resid.get(r.id);
    const why = r.hf ? 'Hjemfald / tilbagekøb'
      : (r.m2p && r.m2p < 25000) ? 'Meget lav kr/m² — fx husbåd eller andelslignende'
      : (d != null && d > 0) ? 'Udbudt langt over sammenlignelige boliger'
      : 'Passer ikke til almindelige boliger i området';
    const a = el('a', { href: r.url || '#', target: '_blank', rel: 'noopener' }, r.adr || '–');
    t.append(el('tr', {},
      el('td', {}, a),
      el('td', {}, r.sub || (r.t === 'villa' ? 'Villa' : 'Ejerlejl.')),
      el('td', { class: 'num' }, r.m2p ? nf(r.m2p) : '–'),
      el('td', { class: 'num' }, d != null ? (d > 0 ? '+' : '') + d + ' %' : '–'),
      el('td', {}, why)));
  });
}

boot();
