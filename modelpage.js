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
    // toFixed is dot-decimal regardless of locale — the rest of the page is da-DK
    { l: 'Forklaringsgrad (R²)', v: fv.r2 != null ? fv.r2.toLocaleString('da-DK', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '–', s: 'af variationen i kr/m²' },
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

/* ===== address-lookup valuation — any address in the region, not just a
   listing. Reuses the already-fitted model (BT.fairValue's predict()) and
   the listings already loaded for the charts above; no new data fetch. ===== */
const haversine_m = (la1, lo1, la2, lo2) => {
  const R = 6371000, p = Math.PI / 180;
  const a = 0.5 - Math.cos((la2 - la1) * p) / 2 + Math.cos(la1 * p) * Math.cos(la2 * p) * (1 - Math.cos((lo2 - lo1) * p)) / 2;
  return 2 * R * Math.asin(Math.sqrt(a));
};

// shared by every gzip+base64 static data file this page lazy-loads —
// nothing in this codebase decoded gzip client-side before #26's address
// lookup, so this is the one decoder the rest reuse.
async function gunzipB64(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  const text = await new Response(stream).text();
  return JSON.parse(text);
}

// data/bbr_lookup.json (18 MB) is real building/unit data keyed by the exact
// address IDs DAWA already gives us (#26 follow-up) — 18 MB isn't something
// every model.html visitor should pay for. Fetched and decoded once, lazily,
// only when the lookup form is actually used, and cached after that.
// Cache the in-flight *promise*, not just the resolved value — otherwise two
// picks made back-to-back before the first ~18 MB fetch/decode finishes each
// start an independent fetch instead of sharing the one already running.
let _bbrLookupPromise = null;
function loadBbrLookup() {
  if (_bbrLookupPromise) return _bbrLookupPromise;
  _bbrLookupPromise = (async () => {
    const doc = await fetch('data/bbr_lookup.json').then(r => r.json());
    const [bFlat, uFlat] = await Promise.all([gunzipB64(doc.buildings), gunzipB64(doc.units)]);
    const buildings = new Map();
    for (let i = 0; i < bFlat.length - 6; i += 7) buildings.set(bFlat[i], bFlat.slice(i + 1, i + 7));
    const units = new Map();
    for (let i = 0; i < uFlat.length - 1; i += 2) units.set(uFlat[i], uFlat[i + 1]);
    return { buildings, units };
  })();
  return _bbrLookupPromise;
}

// data/grundareal.json already exists — fetched earlier this session for
// comps' lot-size matching (annotate_comps in build_data.py), so this is
// free reuse, not a new fetch. Same coarse spatial-hash nearest-point
// lookup as _grundareal_lookup() in build_data.py, ported line-for-line so
// a client lookup returns exactly what the server-side one would: a flat
// [lat,lon,logarea]*N array, 0.01°-cell grid, nearest point in a 3x3
// neighbourhood, decoded back via 2**(logv/scale).
// Same in-flight-promise caching as loadBbrLookup(), and for the same reason.
let _grundarealPromise = null;
function loadGrundareal() {
  if (_grundarealPromise) return _grundarealPromise;
  _grundarealPromise = (async () => {
    const doc = await fetch('data/grundareal.json').then(r => r.json());
    const flat = await gunzipB64(doc.data);
    const scale = doc.logScale || 20.0;
    const CELL = 0.01;
    const grid = new Map();
    for (let i = 0; i < flat.length - 2; i += 3) {
      const la = flat[i], lo = flat[i + 1], logv = flat[i + 2];
      const key = Math.floor(la / CELL) + ',' + Math.floor(lo / CELL);
      (grid.get(key) || grid.set(key, []).get(key)).push([la, lo, logv]);
    }
    // This is nearest-point, not a polygon match (see build_data.py's own
    // _grundareal_lookup docstring) — fine for the ±40% comp-matching
    // tolerance it was built for, but a coordinate with no nearby parcel at
    // all (thin coverage, or just outside the fetched kommuner) can
    // otherwise snap to some unrelated parcel a couple of km away and
    // return a confident-looking but meaningless number. Refuse a match
    // that far off instead — roughly 650 m, well inside the 3x3-cell
    // (~3.3 km) search net.
    const MAX_D = 0.006 ** 2;
    return (lat, lon) => {
      const ci = Math.floor(lat / CELL), cj = Math.floor(lon / CELL);
      let best = null, bestD = null;
      for (let di = -1; di <= 1; di++) {
        for (let dj = -1; dj <= 1; dj++) {
          const pts = grid.get((ci + di) + ',' + (cj + dj));
          if (!pts) continue;
          for (const [la, lo, logv] of pts) {
            const d = (la - lat) ** 2 + (lo - lon) ** 2;
            if (bestD === null || d < bestD) { bestD = d; best = logv; }
          }
        }
      }
      return (best === null || bestD > MAX_D) ? null : Math.round(Math.pow(2, best / scale));
    };
  })();
  return _grundarealPromise;
}

// Station distance, noise, POI and local-comps are all measured per exact
// address at build time — there's no equivalent live data for an address
// that isn't a listing. Rather than fetching/porting a whole separate
// dataset just for this, borrow those fields from the nearest real listings:
// municipality from the single nearest one (categorical), everything else
// averaged over the nearest few. Coarser than a listing's own reading, but
// still a real local reading rather than the model's single global-median
// fallback for genuinely missing data.
function borrowLocationFeatures(listings, lat, lon, k = 8) {
  const near = listings
    .map(r => ({ r, d: haversine_m(lat, lon, r.lat, r.lon) }))
    .sort((a, b) => a.d - b.d)
    .slice(0, k);
  if (!near.length) return {};
  const avg = key => {
    const vals = near.map(n => key(n.r)).filter(v => v != null);
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  };
  const out = { muni: near[0].r.muni, near: near[0].r.near };
  const sst = avg(r => r.sst); if (sst != null) out.sst = sst;
  const mst = avg(r => r.mst); if (mst != null) out.mst = mst;
  const db = avg(r => r.db); if (db != null) out.db = db;
  const cmpM2 = avg(r => r.cmpM2); if (cmpM2 != null) out.cmpM2 = cmpM2;
  const poi = {};
  ['sup', 'sch', 'day'].forEach(k2 => { const v = avg(r => r.poi && r.poi[k2]); if (v != null) poi[k2] = v; });
  if (Object.keys(poi).length) out.poi = poi;
  const geo = {};
  ['mot', 'sea', 'lak', 'grn', 'wat'].forEach(k2 => { const v = avg(r => r.geo && r.geo[k2]); if (v != null) geo[k2] = v; });
  if (Object.keys(geo).length) out.geo = geo;
  return out;
}

function setupLookup() {
  const input = $('#lkAddr'), sug = $('#lkSug'), go = $('#lkGo'), err = $('#lkError'), result = $('#lkResult');
  const seg = $('#lkTypeSeg');
  let items = [], hl = -1, timer, picked = null, lkType = 'villa', pickGen = 0, darIndex = null;
  const SUG_LIMIT = 10;

  // Kick the ~33 MB self-hosted DAR index (#28) off in the background
  // before the first keystroke — someone typically looks at the KPI
  // tiles/charts for a few seconds before touching this field, so it's
  // very likely already loaded by the time they finish typing 3+
  // characters. But NOT synchronously at script-parse time: that raced
  // this 33 MB fetch against the page's own critical listings/sold/meta
  // data for bandwidth and made the whole page feel slow to open. Deferred
  // to idle instead — still well before most visitors start typing, but no
  // longer competing with what's needed to paint the page at all.
  let darPromise = null;
  const startDarPrefetch = () => {
    if (!darPromise) darPromise = BT.loadDarAddresses().then(idx => { darIndex = idx; return idx; });
    return darPromise;
  };
  if ('requestIdleCallback' in window) requestIdleCallback(startDarPrefetch, { timeout: 2000 });
  else setTimeout(startDarPrefetch, 300);

  const close = () => { sug.classList.remove('show'); sug.innerHTML = ''; hl = -1; };
  const mark = () => [...sug.children].forEach((c, i) => c.classList.toggle('hl', i === hl));
  // Only gate the button on the one thing predict() truly can't work without
  // (a housing area) — NOT on having picked an address. A disabled button
  // never fires a click at all, which silently swallowed the "no address
  // picked" case: the error message below existed but could never show.
  const updateGo = () => { go.disabled = !($('#lkArea').value > 0); };

  // A matched house is expanded to one suggestion per unit (kl., st. th/tv,
  // 1. th/tv, ...) when it has any — #28's self-hosted register carries the
  // same floor/door split DAWA's adresser/autocomplete used to give, since
  // floor feeds the model directly. Houses with no units (villas) get one
  // suggestion for the house itself.
  const expandToUnits = matches => {
    const out = [];
    outer:
    for (const r of matches) {
      const units = darIndex.enheder.get(r.id);
      if (units && units.length) {
        const commaIdx = r.text.indexOf(',');
        const head = commaIdx >= 0 ? r.text.slice(0, commaIdx) : r.text;
        const tail = commaIdx >= 0 ? r.text.slice(commaIdx) : '';
        for (const u of units) {
          if (out.length >= SUG_LIMIT) break outer;
          const etageLabel = u.etage === 'st' ? 'st.' : (u.etage ? u.etage + '.' : '');
          const label = [etageLabel, u.doer].filter(Boolean).join(' ');
          out.push({
            text: label ? `${head}, ${label}${tail}` : r.text,
            lat: r.lat, lon: r.lon, husnummerId: r.id,
            unitId: u.id, etage: u.etage, doer: u.doer,
          });
        }
      } else {
        if (out.length >= SUG_LIMIT) break;
        out.push({ text: r.text, lat: r.lat, lon: r.lon, husnummerId: r.id, unitId: null, etage: null, doer: null });
      }
    }
    return out;
  };

  input.addEventListener('input', () => {
    clearTimeout(timer);
    picked = null; updateGo();
    const q = input.value.trim();
    if (q.length < 3) { close(); return; }
    // the common case: darPromise already resolved (prefetched on page
    // load) while the user was typing — only the cold-start case (typing
    // immediately) actually shows this
    if (!darIndex) {
      sug.innerHTML = '';
      sug.append(el('div', { class: 'lk-loading' }, 'Henter adresseliste…'));
      sug.classList.add('show');
    }
    timer = setTimeout(async () => {
      try {
        if (!darIndex) await startDarPrefetch();
        const matches = BT.matchHusnumre(darIndex, q, SUG_LIMIT);
        items = expandToUnits(matches);
        sug.innerHTML = '';
        items.forEach((it, i) => {
          const d = el('div', {}, it.text);
          d.addEventListener('mousedown', ev => { ev.preventDefault(); pick(i); });
          sug.append(d);
        });
        sug.classList.toggle('show', items.length > 0);
      } catch (e) { close(); }
    }, 220);
  });
  input.addEventListener('keydown', e => {
    if (!sug.classList.contains('show')) return;
    if (e.key === 'ArrowDown') { hl = Math.min(items.length - 1, hl + 1); mark(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { hl = Math.max(0, hl - 1); mark(); e.preventDefault(); }
    else if (e.key === 'Enter') { if (hl >= 0) { pick(hl); e.preventDefault(); } }
    else if (e.key === 'Escape') close();
  });
  input.addEventListener('blur', () => setTimeout(close, 150));

  const applyType = type => {
    lkType = type;
    [...seg.children].forEach(c => { const on = c.dataset.type === type; c.classList.toggle('active', on); c.setAttribute('aria-selected', on); });
    $('#lkFloorField').classList.toggle('lk-hide', lkType !== 'condo');
    $('#lkLotField').classList.toggle('lk-hide', lkType !== 'villa');
    $('#lkBsmField').classList.toggle('lk-hide', lkType !== 'villa');
  };
  // "st"/"kl" (stueetage/kælder) aren't plain integers; digits are.
  const parseEtage = e => e == null ? null : e === 'st' ? 0 : /^\d+$/.test(e) ? +e : null;

  const pick = i => {
    const it = items[i];
    input.value = it.text;
    picked = { name: it.text, lat: it.lat, lon: it.lon };
    const myGen = ++pickGen;   // guards against a slower, earlier pick overwriting this one
    // A unit-level address (has etage and/or dør) is a flat; a plain street
    // address is a house or rækkehus, both "villa" here. Detect both ways —
    // this used to only ever switch *to* condo, so after looking up a flat,
    // the next lookup of a house stayed on Ejerlejlighed and silently valued
    // it as one (boligtype is a model feature, and it also decides whether
    // grund/kælder or etage are used at all).
    if (it.etage != null || it.doer != null) {
      applyType('condo');
      const fl = parseEtage(it.etage);
      if (fl != null) $('#lkFloor').value = fl;
    } else {
      applyType('villa');
      $('#lkFloor').value = '';   // don't carry a previous flat's floor over
    }
    close(); updateGo();

    // BBR auto-fill (#26 follow-up): real area and year, keyed by exactly
    // the IDs this pick already carries — no BFE/EBR hop needed. Wall/roof/
    // heating codes exist in the same file but aren't surfaced: they're
    // opaque BBR kodeliste numbers with no label mapping, and the earlier
    // ablation found they don't improve the model anyway. Area and year DO
    // feed directly into real, already-working model features, so they're
    // worth the fetch.
    err.textContent = 'Henter BBR-data…';
    const bbrDone = loadBbrLookup().then(({ buildings, units }) => {
      if (myGen !== pickGen) return;   // a newer pick has since replaced this one
      const bld = buildings.get(it.husnummerId);
      if (bld && bld[0]) $('#lkYear').value = bld[0];
      if (it.unitId) {
        const areaM2 = units.get(it.unitId);
        if (areaM2) { $('#lkArea').value = areaM2; updateGo(); }
      }
    });
    // Grundstørrelse: data/grundareal.json already exists (fetched earlier
    // this session for comps' lot-size matching) — no new fetch, just the
    // same coordinate the address pick already gave us.
    const lotDone = loadGrundareal().then(lookup => {
      if (myGen !== pickGen) return;
      const lotM2 = lookup(picked.lat, picked.lon);
      if (lotM2) $('#lkLot').value = lotM2;
    });
    Promise.allSettled([bbrDone, lotDone]).then(() => { if (myGen === pickGen) err.textContent = ''; });
  };

  seg.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    applyType(b.dataset.type);
  });
  applyType(lkType);

  $('#lkArea').addEventListener('input', updateGo);

  go.addEventListener('click', () => {
    err.textContent = ''; result.classList.remove('show');
    if (!DATA) { err.textContent = 'Data indlæses stadig — prøv igen om lidt.'; return; }
    if (!picked) { err.textContent = 'Vælg en adresse fra listen.'; return; }
    const area = +$('#lkArea').value;
    if (!(area > 0)) { err.textContent = 'Angiv boligareal.'; return; }
    const fv = BT.fairValue(DATA.listings, DATA.sold);
    if (!fv.predict) { err.textContent = 'Modellen kunne ikke beregnes (for få boliger i datasættet).'; return; }
    const loc = borrowLocationFeatures(DATA.listings, picked.lat, picked.lon);
    const spec = Object.assign({
      t: lkType, a: area,
      r: +$('#lkRooms').value || 0,
      y: +$('#lkYear').value || undefined,
      e: $('#lkEnergy').value || undefined,
      fln: lkType === 'condo' ? (+$('#lkFloor').value || 0) : undefined,
      bsm: lkType === 'villa' ? (+$('#lkBsm').value || 0) : 0,
      lot: lkType === 'villa' ? (+$('#lkLot').value || 0) : 0,
    }, loc);
    const pm2 = fv.predict(spec);
    if (!pm2) { err.textContent = 'Kunne ikke beregne en vurdering for denne adresse.'; return; }
    $('#lkPrice').textContent = nf(pm2 * area) + ' kr · ' + m2(pm2);
    $('#lkBand').textContent = (fv.lo && fv.hi)
      ? `Sandsynligt spænd: ${nf(pm2 * area * fv.lo)}–${nf(pm2 * area * fv.hi)} kr`
      : '';
    result.classList.add('show');
  });
}
setupLookup();

boot();
