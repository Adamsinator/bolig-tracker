'use strict';

/* ===================== state & helpers ===================== */
const S = {
  meta: null, geo: null, index: null, history: null,
  all: [], type: 'all', munis: new Set(), nearTransit: null,
  priceMin: null, priceMax: null, rooms: null, areaMin: null, areaMax: null,
  lotMin: null, floorMin: null, yearMin: null, daysMax: null, energyMin: null,
  poiSup: null, poiSch: null, poiDay: null,   // max metres to shop/school/daycare (#7)
  hasBasement: false, hasElevator: false, hasBalcony: false,
  search: '', colorBy: 'm2p', sort: 'd', shown: 60, showRail: true, showMetro: true, showPois: false, trackerMap: null, onlyCut: false,
  favs: {}, onlyFav: false, cmpA: null, cmpB: null, onlyNew: false, hideHf: false,
  A: null, B: null, radA: 3, radB: 3,   // home/work points {name,lat,lon}
  dstArea: '01', indexMode: 'krm2', bvc: null,
};
// Boliger now sits above the map/charts, so its initial page must stay short
// or it pushes everything else off-screen — especially on a phone. Collapsed
// to 5 (mobile) / 10 (desktop, 2 rows at 5-per-row) until "Vis flere" is hit.
const MOBILE_MQ = matchMedia('(max-width:640px)');
const collapsedCount = () => MOBILE_MQ.matches ? 5 : 10;
const $ = (s, r = document) => r.querySelector(s);
const el = (t, a = {}, ...kids) => {
  const n = document.createElement(t);
  for (const k in a) { if (k === 'class') n.className = a[k]; else if (k === 'html') n.innerHTML = a[k]; else n.setAttribute(k, a[k]); }
  for (const c of kids) if (c != null) n.append(c.nodeType ? c : document.createTextNode(c));
  return n;
};
// HTML-escape untrusted text (listing addresses, city/station names, user input)
// before it goes into innerHTML / Leaflet tooltips. el() text children are already
// safe (createTextNode above); this covers the template-literal sinks.
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const SVGNS = 'http://www.w3.org/2000/svg';
const svel = (t, a = {}) => { const n = document.createElementNS(SVGNS, t); for (const k in a) n.setAttribute(k, a[k]); return n; };

// "100 m² · 3 vær." — skips missing parts so we never render "null vær."
const dims = r => [r.a ? `${r.a} m²` : null, r.r ? `${r.r} vær.` : null].filter(Boolean).join(' · ');
const kr = n => n == null ? '–' : Math.round(n).toLocaleString('da-DK') + ' kr';
const krM = n => n == null ? '–' : (n >= 1e6 ? (n / 1e6).toLocaleString('da-DK', { maximumFractionDigits: 2 }) + ' mio. kr'
  : Math.round(n / 1000).toLocaleString('da-DK') + '.000 kr');
const m2 = n => n == null ? '–' : Math.round(n).toLocaleString('da-DK') + ' kr/m²';
const num = n => n == null ? '–' : Math.round(n).toLocaleString('da-DK');
const median = arr => { if (!arr.length) return null; const a = [...arr].sort((x, y) => x - y); const m = a.length >> 1; return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; };
const quantile = (arr, q) => { if (!arr.length) return null; const a = [...arr].sort((x, y) => x - y); const p = (a.length - 1) * q, lo = Math.floor(p); return a[lo] + (a[lo + 1] - a[lo] || 0) * (p - lo); };
const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
// Evenly-spaced tick indices into an n-long axis, always including the last
// one. Appending it unconditionally (rather than swapping it in) could land
// it less than a full step past the previous tick — e.g. quarterly axes as
// more data accumulates, or decade axes with gaps — crowding two labels into
// unreadable overlapping text.
const thinTicks = (n, maxLabels) => {
  const step = Math.max(1, Math.ceil((n - 1) / (maxLabels - 1))), idx = [];
  for (let i = 0; i < n; i += step) idx.push(i);
  const last = n - 1;
  if (idx[idx.length - 1] !== last) {
    if (last - idx[idx.length - 1] < step) idx[idx.length - 1] = last; else idx.push(last);
  }
  return idx;
};
// one colour per entity: ejerlejlighed = blue, villa = orange (blue when both)
const typeColor = () => cssVar(S.type === 'villa' ? '--villa' : '--condo');
const haversine = (la1, lo1, la2, lo2) => {
  const R = 6371, p = Math.PI / 180;
  const a = 0.5 - Math.cos((la2 - la1) * p) / 2 + Math.cos(la1 * p) * Math.cos(la2 * p) * (1 - Math.cos((lo2 - lo1) * p)) / 2;
  return 2 * R * Math.asin(Math.sqrt(a)); // km
};
const ENERGY_ORDER = { a: 7, b: 6, c: 5, d: 4, e: 3, f: 2, g: 1 };
const energyRank = e => { if (!e) return 0; return ENERGY_ORDER[String(e)[0].toLowerCase()] || 0; };
const PRICES = [1e6, 1.5e6, 2e6, 2.5e6, 3e6, 4e6, 5e6, 7.5e6, 10e6, 15e6, 20e6, 30e6, 50e6, 75e6, 100e6, 150e6];

/* ===================== load ===================== */
async function boot() {
  try {
    const [meta, listings, geo, index, history, bvc, sold, poi] = await Promise.all([
      fetch('data/meta.json').then(r => r.json()),
      fetch('data/listings.json').then(r => r.json()),
      fetch('data/geo.json').then(r => r.json()).catch(() => ({})),
      fetch('data/priceindex.json').then(r => r.json()).catch(() => null),
      fetch('data/history.json').then(r => r.json()).catch(() => ({ series: [] })),
      fetch('data/bvc.json').then(r => r.json()).catch(() => null),
      fetch('data/sold.json').then(r => r.json()).catch(() => null),
      fetch('data/poi.json').then(r => r.json()).catch(() => null),
    ]);
    S.meta = meta; S.all = listings; S.geo = geo; S.index = index; S.history = history; S.bvc = bvc; S.sold = sold; S.poi = poi;
    meta.municipalities.forEach(m => S.munis.add(m.slug));
    S.favs = loadFavs();    // saved homes (device-local)
    decodeState();          // apply any filters carried in the URL
    S.shown = collapsedCount();
    initUI();
    initMap();
    render();
    loadTracker();
  } catch (e) {
    $('#map').innerHTML = '<div class="loading">Kunne ikke hente data.</div>';
    console.error(e);
  }
}

/* ===================== UI ===================== */
function initUI() {
  const upd = new Date(S.meta.generatedAt);
  $('#updated').textContent = 'Opdateret ' + upd.toLocaleDateString('da-DK', { day: 'numeric', month: 'long', year: 'numeric' }) +
    ' · ' + S.meta.total.toLocaleString('da-DK') + ' boliger';
  $('#nearSHint').textContent = '(S-tog ≤ ' + S.meta.strainNearM + ' m · metro ≤ ' + METRO_NEAR_M + ' m)';

  // price selects
  const fill = (id, label) => { const s = $(id); PRICES.forEach(p => s.append(el('option', { value: p }, krM(p).replace(' kr', '')))); };
  fill('#priceMin'); fill('#priceMax');

  $('#typeSeg').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    S.type = b.dataset.type; S.shown = collapsedCount();
    [...e.currentTarget.children].forEach(c => c.classList.toggle('active', c === b));
    render();
  });

  // municipality chips
  const wrap = $('#muniChips');
  // make a chip behave like a button for both mouse and keyboard users
  const chipKb = (elm, fn) => {
    elm.setAttribute('role', 'button'); elm.tabIndex = 0;
    elm.addEventListener('click', fn);
    elm.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn(); } });
  };
  const allBtn = el('span', { class: 'chip allbtn on' }, 'Alle kommuner');
  chipKb(allBtn, () => {
    const allOn = S.munis.size === S.meta.municipalities.length;
    S.munis = new Set(allOn ? [] : S.meta.municipalities.map(m => m.slug));
    S.shown = collapsedCount(); autoFollowDstArea(); autoFollowCompare(); render(); fitToSelection();
  });
  wrap.append(allBtn);
  S.meta.municipalities.forEach(m => {
    const c = el('span', { class: 'chip on' }, m.name);
    chipKb(c, () => {
      if (S.munis.has(m.slug)) S.munis.delete(m.slug); else S.munis.add(m.slug);
      S.shown = collapsedCount(); autoFollowDstArea(); autoFollowCompare(); render(); fitToSelection();
    });
    c._slug = m.slug; wrap.append(c);
  });
  // mobile: collapsed to "Alle kommuner" + first 5 by default, with a toggle
  // to expand back to the full wrapped grid (desktop always shows it all —
  // the toggle itself is hidden there via CSS, this only matters on mobile)
  wrap.classList.add('collapsed');
  const muniToggle = el('span', { class: 'chip muni-toggle' }, `Vis alle ${S.meta.municipalities.length} kommuner`);
  chipKb(muniToggle, () => {
    const collapsed = wrap.classList.toggle('collapsed');
    muniToggle.textContent = collapsed ? `Vis alle ${S.meta.municipalities.length} kommuner` : 'Vis færre';
  });
  wrap.append(muniToggle);

  // filters
  const on = (id, key, isNum) => $(id).addEventListener('change', e => {
    const v = e.target.type === 'checkbox' ? e.target.checked : (e.target.value || null);
    S[key] = isNum && v ? +v : v; S.shown = collapsedCount(); render();
  });
  on('#priceMin', 'priceMin', 1); on('#priceMax', 'priceMax', 1); on('#rooms', 'rooms', 1);
  on('#areaMin', 'areaMin', 1); on('#areaMax', 'areaMax', 1); on('#lotMin', 'lotMin', 1);
  on('#floorMin', 'floorMin', 1); on('#yearMin', 'yearMin', 1); on('#daysMax', 'daysMax', 1);
  on('#poiSup', 'poiSup', 1); on('#poiSch', 'poiSch', 1); on('#poiDay', 'poiDay', 1);
  on('#energyMin', 'energyMin'); on('#hasBasement', 'hasBasement'); on('#hasElevator', 'hasElevator');
  on('#hasBalcony', 'hasBalcony'); on('#colorBy', 'colorBy'); on('#sort', 'sort');
  on('#nearTransit', 'nearTransit'); on('#onlyCut', 'onlyCut'); on('#onlyFav', 'onlyFav'); on('#onlyNew', 'onlyNew'); on('#hideHf', 'hideHf');
  $('#showRail').addEventListener('change', e => { S.showRail = e.target.checked; applyRailVisibility(); renderMapLegend(S.colorBy, filtered(), LINE_COLORS()); });
  $('#showMetro').addEventListener('change', e => { S.showMetro = e.target.checked; applyTransitVisibility(); renderMapLegend(S.colorBy, filtered(), LINE_COLORS()); });
  { const pc = $('#showPois'); if (pc) pc.addEventListener('change', e => { S.showPois = e.target.checked; drawPois(); renderMapLegend(S.colorBy, filtered(), LINE_COLORS()); }); }
  $('#search').addEventListener('input', e => {
    S.search = e.target.value.toLowerCase().trim(); S.shown = collapsedCount(); render();
    // A postnummer (4-digit token, e.g. 2900) zooms the map to that area only;
    // clearing the search restores the current kommune view.
    if (/\b\d{4}\b/.test(S.search)) fitToPoints(filtered());
    else if (!S.search) fitToSelection();
  });
  $('#loadMore').addEventListener('click', () => { S.shown += 30; renderCards(filtered()); });
  $('#resetFilters').addEventListener('click', resetFilters);

  // DST area select — only the corridor landsdele we can anchor to real kr/m²
  if (S.index) {
    const sel = $('#dstArea');
    S.index.areas.filter(a => DST_LANDSDEL_MUNIS[a.id]).forEach(a => sel.append(el('option', { value: a.id }, a.name)));
    sel.value = S.dstArea;
    sel.addEventListener('change', e => { S.dstArea = e.target.value; renderIndexChart(); });
  }
  const modeSel = $('#indexMode');
  if (!S.bvc) modeSel.querySelector('option[value="real"]').remove();
  modeSel.addEventListener('change', e => {
    S.indexMode = e.target.value;
    $('#dstAreaLabel').style.display = S.indexMode === 'real' ? 'none' : '';
    renderIndexChart();
  });
  $('#trendMetric').addEventListener('change', renderTrendChart);

  // compare two kommuner
  const munis = S.meta.municipalities;
  const has = s => munis.some(m => m.slug === s);
  S.cmpA = has('koebenhavn') ? 'koebenhavn' : munis[0].slug;
  S.cmpB = has('gentofte') ? 'gentofte' : (munis[1] || munis[0]).slug;
  [['#cmpA', 'cmpA'], ['#cmpB', 'cmpB']].forEach(([id, key]) => {
    const sel = $(id);
    munis.forEach(m => sel.append(el('option', { value: m.slug }, m.name)));
    sel.value = S[key];
    sel.addEventListener('change', e => { S[key] = e.target.value; renderCompare(); });
  });
  const favN = Object.keys(S.favs).length; if (favN) $('#onlyFavLabel').textContent = `Kun gemte (${favN})`;

  // (map pan / zoom / double-click zoom is native Leaflet)

  // home / work geocoding
  setupGeo('A'); setupGeo('B');
  $('#radA').addEventListener('input', e => { S.radA = +e.target.value; $('#radAVal').textContent = e.target.value; render(); });
  $('#radB').addEventListener('input', e => { S.radB = +e.target.value; $('#radBVal').textContent = e.target.value; render(); });

  // theme
  const tt = $('#themeToggle');
  const saved = localStorage.getItem('hbTheme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
  tt.addEventListener('click', () => {
    const cur = document.documentElement.getAttribute('data-theme');
    const dark = cur ? cur === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.setAttribute('data-theme', dark ? 'light' : 'dark');
    localStorage.setItem('hbTheme', dark ? 'light' : 'dark');
    refreshMapTheme(); render();
  });

  autoFollowDstArea();       // point the price-index area at the selected kommuner
  autoFollowCompare();       // default the compare card to a 2-kommune selection
  syncControlsFromState();   // reflect any URL-provided filters in the controls
}

function resetFilters() {
  Object.assign(S, { priceMin: null, priceMax: null, rooms: null, areaMin: null, areaMax: null, lotMin: null,
    floorMin: null, yearMin: null, daysMax: null, energyMin: null, poiSup: null, poiSch: null, poiDay: null, hasBasement: false, hasElevator: false, hasBalcony: false, onlyCut: false, onlyNew: false, hideHf: false, nearTransit: null });
  ['#priceMin', '#priceMax', '#rooms', '#areaMin', '#areaMax', '#lotMin', '#floorMin', '#yearMin', '#daysMax', '#energyMin', '#poiSup', '#poiSch', '#poiDay'].forEach(id => $(id).value = '');
  ['#hasBasement', '#hasElevator', '#hasBalcony', '#onlyCut', '#onlyNew', '#hideHf'].forEach(id => $(id).checked = false);
  const nt = $('#nearTransit'); if (nt) nt.value = '';
  S.shown = collapsedCount(); render();
}
function activeFilterCount() {
  let n = 0;
  ['priceMin', 'priceMax', 'rooms', 'areaMin', 'areaMax', 'lotMin', 'floorMin', 'yearMin', 'daysMax', 'energyMin', 'poiSup', 'poiSch', 'poiDay'].forEach(k => { if (S[k]) n++; });
  ['hasBasement', 'hasElevator', 'hasBalcony', 'onlyCut', 'onlyNew', 'hideHf'].forEach(k => { if (S[k]) n++; });
  if (S.nearTransit) n++;
  return n;
}

/* ---- shareable state in the URL (so a filtered view can be bookmarked) ---- */
const NUM_KEYS = ['priceMin', 'priceMax', 'rooms', 'areaMin', 'areaMax', 'lotMin', 'floorMin', 'yearMin', 'daysMax'];
const CHECK_KEYS = ['hasBasement', 'hasElevator', 'hasBalcony', 'onlyCut', 'onlyNew', 'hideHf'];
const TRANSIT_VALS = ['s', 'metro', 'any'];
function decodeState() {
  const p = new URLSearchParams(location.search);
  if (![...p.keys()].length) return;
  const t = p.get('type'); if (t === 'condo' || t === 'villa') S.type = t;
  if (p.get('muni')) {
    const valid = p.get('muni').split(',').filter(s => S.meta.municipalities.some(m => m.slug === s));
    if (valid.length) S.munis = new Set(valid);
  }
  if (p.get('q')) S.search = p.get('q').toLowerCase().trim();
  NUM_KEYS.forEach(k => { const v = p.get(k); if (v != null && v !== '' && !isNaN(+v)) S[k] = +v; });
  if (p.get('energyMin')) S.energyMin = p.get('energyMin');
  CHECK_KEYS.forEach(k => { if (p.get(k) === '1') S[k] = true; });
  if (TRANSIT_VALS.includes(p.get('nearTransit'))) S.nearTransit = p.get('nearTransit');
  if (p.get('sort')) S.sort = p.get('sort');
  if (p.get('colorBy')) S.colorBy = p.get('colorBy');
}
function encodeState() {
  if (!S.meta) return;
  const p = new URLSearchParams();
  if (S.type !== 'all') p.set('type', S.type);
  if (S.munis.size && S.munis.size < S.meta.municipalities.length) p.set('muni', [...S.munis].join(','));
  if (S.search) p.set('q', S.search);
  NUM_KEYS.forEach(k => { if (S[k] != null) p.set(k, S[k]); });
  if (S.energyMin) p.set('energyMin', S.energyMin);
  CHECK_KEYS.forEach(k => { if (S[k]) p.set(k, '1'); });
  if (S.nearTransit) p.set('nearTransit', S.nearTransit);
  if (S.sort !== 'd') p.set('sort', S.sort);
  if (S.colorBy !== 'm2p') p.set('colorBy', S.colorBy);
  const qs = p.toString();
  try { history.replaceState(null, '', qs ? '?' + qs : location.pathname); } catch (e) { /* ignore */ }
}
function syncControlsFromState() {
  [['#priceMin', 'priceMin'], ['#priceMax', 'priceMax'], ['#rooms', 'rooms'], ['#areaMin', 'areaMin'], ['#areaMax', 'areaMax'], ['#lotMin', 'lotMin'], ['#floorMin', 'floorMin'], ['#yearMin', 'yearMin'], ['#daysMax', 'daysMax'], ['#energyMin', 'energyMin'], ['#sort', 'sort'], ['#colorBy', 'colorBy']]
    .forEach(([id, k]) => { if (S[k] != null) $(id).value = S[k]; });
  CHECK_KEYS.forEach(k => { const elc = $('#' + k); if (elc) elc.checked = !!S[k]; });
  { const nt = $('#nearTransit'); if (nt) nt.value = S.nearTransit || ''; }
  $('#search').value = S.search || '';
  [...$('#typeSeg').children].forEach(b => b.classList.toggle('active', b.dataset.type === S.type));
  if (activeFilterCount()) $('#moreFilters').open = true;
}

/* ---- address autocomplete — self-hosted DAR register (#28), no live
   third-party API. Building-level only (a coordinate is all this needs;
   unit/floor detail is model.html's address-lookup page's concern, not
   the commute-radius picker's). ---- */
function setupGeo(which) {
  const input = $('#addr' + which), sug = $('#sug' + which);
  let items = [], hl = -1, timer, darIndex = null;
  const close = () => { sug.classList.remove('show'); sug.innerHTML = ''; hl = -1; };
  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 3) { close(); return; }
    timer = setTimeout(async () => {
      try {
        if (!darIndex) darIndex = await BT.loadDarHusnumre();
        items = BT.matchHusnumre(darIndex, q, 6);
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
  const mark = () => [...sug.children].forEach((c, i) => c.classList.toggle('hl', i === hl));
  const pick = i => {
    const it = items[i];
    input.value = it.text;
    S[which] = { name: it.text, lat: it.lat, lon: it.lon };
    close(); render(); fitToSelection();
  };
}

/* ===================== filtering ===================== */
const NEW_DAYS = 14;   // "Ny på markedet" = on the market at most this many days
const METRO_NEAR_M = 500;   // tight catchment: metro only serves dense central
// kommuner where almost everything is within ~1 km of a stop, so 500 m is what
// actually separates "near" from "far" and keeps the metro premium meaningful.
const nearMetro = r => r.mst != null && r.mst <= METRO_NEAR_M;
function filtered() {
  return S.all.filter(r => {
    if (S.type !== 'all' && r.t !== S.type) return false;
    if (!S.munis.has(r.muni)) return false;
    if (S.nearTransit === 's' && !r.near) return false;
    if (S.nearTransit === 'metro' && !nearMetro(r)) return false;
    if (S.nearTransit === 'any' && !(r.near || nearMetro(r))) return false;
    if (S.priceMin && r.p < S.priceMin) return false;
    if (S.priceMax && r.p > S.priceMax) return false;
    if (S.rooms && (r.r || 0) < S.rooms) return false;
    if (S.areaMin && (r.a || 0) < S.areaMin) return false;
    if (S.areaMax && (r.a || 0) > S.areaMax) return false;
    if (S.lotMin && (r.lot || 0) < S.lotMin) return false;          // villa lots
    if (S.floorMin != null && r.t === 'condo' && (r.fln == null || r.fln < S.floorMin)) return false;
    if (S.yearMin && (r.y || 0) < S.yearMin) return false;
    if (S.daysMax && (r.d || 0) > S.daysMax) return false;
    if (S.energyMin && energyRank(r.e) < energyRank(S.energyMin)) return false;
    if (S.poiSup && !(r.poi && r.poi.sup != null && r.poi.sup <= S.poiSup)) return false;
    if (S.poiSch && !(r.poi && r.poi.sch != null && r.poi.sch <= S.poiSch)) return false;
    if (S.poiDay && !(r.poi && r.poi.day != null && r.poi.day <= S.poiDay)) return false;
    if (S.hasBasement && !(r.bsm > 0)) return false;
    if (S.hasElevator && !r.elev) return false;
    if (S.hasBalcony && !r.balc) return false;
    if (S.onlyCut && !(r.chg < 0)) return false;                    // only price-reduced
    if (S.onlyNew && !(r.d != null && r.d <= NEW_DAYS)) return false; // only new listings
    if (S.hideHf && r.hf) return false;                              // hide land-reversion homes
    if (S.onlyFav && !S.favs[String(r.id)]) return false;           // only saved homes
    if (S.A && haversine(r.lat, r.lon, S.A.lat, S.A.lon) > S.radA) return false;
    if (S.B && haversine(r.lat, r.lon, S.B.lat, S.B.lon) > S.radB) return false;
    if (S.search) {
      const hay = (r.adr + ' ' + r.city + ' ' + r.zip + ' ' + (r.ssn || '')).toLowerCase();
      if (!hay.includes(S.search)) return false;
    }
    return true;
  });
}

/* ===================== master render ===================== */
function render() {
  [...$('#muniChips').children].forEach(c => {
    const on = c._slug ? S.munis.has(c._slug) : S.munis.size === S.meta.municipalities.length;
    c.classList.toggle('on', on);
    c.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  const fc = activeFilterCount();
  $('#filterCount').textContent = fc ? `(${fc} aktive)` : '';
  const f = filtered();
  renderKPIs(f);
  renderMuniChart(f);
  renderSold(f);
  renderSoldTrend(f);
  renderSoldDecade();
  renderDistChart(f);
  renderDaysChart(f);
  renderYearChart(f);
  renderScatter(f);
  renderPriceChanges(f);
  renderIndexChart();
  renderTrendChart();
  renderCompare();
  drawMap(f);
  renderCards(f);
  encodeState();
}

/* ===================== KPIs ===================== */
function renderKPIs(f) {
  const prices = f.map(r => r.p).filter(Boolean);
  const m2p = f.map(r => r.m2p).filter(Boolean);
  const days = f.map(r => r.d).filter(v => v != null);
  const cutRows = f.filter(r => r.chg < 0);
  const cutPct = f.length ? Math.round(cutRows.length / f.length * 100) : 0;
  const cutMed = cutRows.length ? median(cutRows.map(r => Math.abs(r.chg))) : null;
  const typeLabel = S.type === 'all' ? 'ejerl. + villaer' : (S.type === 'condo' ? 'ejerlejligheder' : 'villaer');
  const kpis = [
    { label: 'Boliger til salg', val: f.length.toLocaleString('da-DK'), sub: typeLabel },
    { label: 'Median pris', val: krM(median(prices)), sub: '' },
    { label: 'Median pris/m²', val: m2(median(m2p)), sub: 'typisk kvadratmeterpris' },
    // days-on-market for homes still listed *right now* (boligsiden's own
    // daysOnMarket, a running count that hasn't stopped since none of these
    // have sold) — not time-to-sell for completed transactions. A snapshot
    // of active listings skews toward slow sellers (fast ones exit the pool
    // quickly), so this reads higher than a true average time-to-sell would.
    { label: 'Median liggetid', val: median(days) != null ? Math.round(median(days)) + ' <small>dage</small>' : '–',
      sub: 'boliger til salg nu — ikke fuldført salgstid', html: true,
      title: 'Hvor længe de boliger, der er til salg lige nu, har ventet indtil videre — ikke hvor lang tid det i gennemsnit tager at sælge en bolig (den tæller for boliger, der endnu ikke er solgt).' },
    { label: 'Andel med prisnedslag', val: cutPct + ' <small>%</small>',
      sub: cutMed != null ? `af boligerne — typisk ${cutMed.toLocaleString('da-DK', { maximumFractionDigits: 1 })} % under første pris` : 'af boligerne har sat prisen ned', html: true },
  ];
  const box = $('#kpis'); box.innerHTML = ''; box.removeAttribute('aria-busy');
  kpis.forEach(k => box.append(el('div', { class: 'kpi', ...(k.title ? { title: k.title } : {}) },
    el('div', { class: 'k-label' }, k.label),
    el('div', { class: 'k-val', html: k.val }),
    el('div', { class: 'k-sub' }, k.sub || ''))));
}

/* ===================== tooltip ===================== */
const TT = $('#tooltip');
function showTip(html, x, y) {
  TT.innerHTML = html; TT.hidden = false;
  const w = TT.offsetWidth, h = TT.offsetHeight;
  TT.style.left = Math.min(x + 14, innerWidth - w - 8) + 'px';
  TT.style.top = Math.max(8, y - h - 12) + 'px';
}
const hideTip = () => { TT.hidden = true; };

/* ===================== vertical column chart ===================== */
// compact kroner: 86.600 -> "87k"
const kc = v => v >= 1000 ? Math.round(v / 1000) + 'k' : Math.round(v);

// Asking (current listings) vs. realised (registered sales, last 12 mo) median
// kr/m² per kommune. A positive gap = asking prices sit above what homes actually
// fetch — the classic asking-optimism, biggest in slow, high-end condo markets.
function soldForMuni(slug) {
  const per = S.sold && S.sold.byMuni[slug]; if (!per) return null;
  if (S.type === 'condo' || S.type === 'villa') { const a = per[S.type]; return a ? { m2: a.medM2, n: a.n, size: a.medSize, year: a.medYear } : null; }
  let num = 0, den = 0;                    // "all": pool the two types by sale count
  const a = per.condo && per.condo.n >= (per.villa ? per.villa.n : 0) ? per.condo : (per.villa || per.condo);
  for (const t of ['condo', 'villa']) { const x = per[t]; if (x) { num += x.medM2 * x.n; den += x.n; } }
  return den ? { m2: Math.round(num / den), n: den, size: a && a.medSize, year: a && a.medYear } : null;
}
function renderSold(f) {
  const box = $('#chartSold'), note = $('#soldNote'); if (!box) return;
  box.innerHTML = '';
  if (!S.sold) { box.append(el('div', { class: 'loading' }, 'Realiserede salgspriser er ikke tilgængelige.')); if (note) note.textContent = ''; return; }
  const names = Object.fromEntries(S.meta.municipalities.map(m => [m.slug, m.name]));
  const askByM = new Map();
  f.forEach(r => { if (r.m2p) (askByM.get(r.muni) || askByM.set(r.muni, []).get(r.muni)).push(r.m2p); });
  const rows = [];
  for (const m of S.meta.municipalities) {
    const ask = median((askByM.get(m.slug) || []).filter(Boolean));
    const s = soldForMuni(m.slug);
    // gap = realiseret ift. udbud → negative when asking sits above realised (the usual case)
    if (ask && s && s.m2) rows.push({ label: names[m.slug] || m.slug, ask: Math.round(ask), sold: s.m2, n: s.n, gap: Math.round((s.m2 / ask - 1) * 100) });
  }
  rows.sort((a, b) => a.gap - b.gap);   // most negative (asking most above realised) first
  if (!rows.length) { box.append(el('div', { class: 'loading' }, 'Ingen overlap mellem udbud og salg for det valgte filter.')); if (note) note.textContent = ''; return; }

  const askC = cssVar('--ink-2'), soldC = cssVar('--condo');
  const W = 760, H = 320, padL = 44, padR = 12, padT = 26, padB = 84;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const max = Math.max(...rows.flatMap(r => [r.ask, r.sold])) * 1.1 || 1;
  const svg = svel('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' });
  for (let g = 0; g <= 4; g++) {
    const y = padT + plotH - g / 4 * plotH;
    svg.append(svel('line', { x1: padL, y1: y, x2: W - padR, y2: y, class: 'gridline' }));
    const t = svel('text', { x: padL - 6, y: y + 3, 'text-anchor': 'end', class: 'axis-txt' }); t.textContent = kc(max * g / 4); svg.append(t);
  }
  // inline legend
  [['Udbud', askC], ['Realiseret', soldC]].forEach(([lab, col], i) => {
    const lx = padL + i * 110;
    svg.append(svel('rect', { x: lx, y: 6, width: 11, height: 11, rx: 2, fill: col }));
    const t = svel('text', { x: lx + 16, y: 15, class: 'axis-txt' }); t.textContent = lab; svg.append(t);
  });
  const bw = plotW / rows.length;
  rows.forEach((r, i) => {
    const gx = padL + i * bw, w = bw * .30;
    const g = svel('g');
    [[r.ask, askC, gx + bw * .16], [r.sold, soldC, gx + bw * .52]].forEach(([v, col, x]) => {
      const h = v / max * plotH, y = padT + plotH - h;
      g.append(svel('rect', { x, y, width: w, height: h, rx: 3, fill: col }));
    });
    // gap % label above the taller bar
    const topY = padT + plotH - Math.max(r.ask, r.sold) / max * plotH - 5;
    const gt = svel('text', { x: gx + bw / 2, y: topY, 'text-anchor': 'middle', class: 'bar-val' });
    gt.textContent = (r.gap >= 0 ? '+' : '') + r.gap + '%'; g.append(gt);
    g.addEventListener('mousemove', e => showTip(
      `<div class="tt-title">${r.label}</div>` +
      `<div class="tt-row"><span>Udbud nu</span><b>${m2(r.ask)}</b></div>` +
      `<div class="tt-row"><span>Realiseret (12 mdr.)</span><b>${m2(r.sold)}</b></div>` +
      `<div class="tt-row"><span>Forskel</span><b>${(r.gap >= 0 ? '+' : '') + r.gap} %</b></div>` +
      `<div class="tt-row"><span>Antal salg</span><b>${r.n}</b></div>`, e.clientX, e.clientY));
    g.addEventListener('mouseleave', hideTip);
    svg.append(g);
    const lx = gx + bw / 2, ly = H - padB + 15;
    const lt = svel('text', { x: lx, y: ly, class: 'axis-txt', 'text-anchor': 'end', transform: `rotate(-40 ${lx} ${ly})` }); lt.textContent = r.label; svg.append(lt);
  });
  box.append(svg);
  if (note) {
    const gaps = rows.map(r => r.gap).sort((a, b) => a - b);
    const medGap = gaps[gaps.length >> 1];
    const ttxt = S.type === 'condo' ? 'ejerlejligheder' : S.type === 'villa' ? 'villaer' : 'alle boligtyper';
    let txt = `Negativ forskel = de faktisk tinglyste salgspriser ligger under udbudspriserne. Medianforskel (${ttxt}): ${(medGap >= 0 ? '+' : '') + medGap} %.`;
    const mix = S.sold && S.sold.saleMix && S.sold.saleMix.pct;
    if (mix) {
      const parts = [];
      if (mix.almindelig != null) parts.push(`${mix.almindelig} % almindelige`);
      if (mix.familie != null) parts.push(`${mix.familie} % familiehandler`);
      if (mix.auktion != null) parts.push(`${mix.auktion} % auktioner`);
      if (parts.length) txt += ` Handelstyper (seneste 12 mdr.): ${parts.join(', ')}.`;
    }
    note.textContent = txt + ' Kilde: Boliga/tinglysning.';
  }
}

// Realised kr/m² per quarter over the last ~2 years. Across several kommuner it's
// a median of the per-kommune quarterly medians (exact when one kommune is
// selected). A dashed line marks the current asking level so you can see where
// today's listings sit relative to the realised trajectory.
function renderSoldTrend(f) {
  const mount = $('#chartSoldTrend'), note = $('#soldTrendNote'); if (!mount) return;
  mount.innerHTML = '';
  if (!S.sold || !S.sold.series) { mount.append(el('div', { class: 'loading' }, 'Realiserede salgspriser er ikke tilgængelige.')); if (note) note.textContent = ''; return; }
  const names = Object.fromEntries(S.meta.municipalities.map(m => [m.slug, m.name]));
  const quarters = S.sold.quarters || [];
  const sel = [...S.munis];
  const scopeName = S.munis.size >= S.meta.municipalities.length ? 'hele korridoren'
    : S.munis.size === 1 ? (names[sel[0]] || sel[0]) : `${S.munis.size} kommuner`;
  // For the whole corridor use the pipeline's pooled series (volume-weighted, so
  // condo kr/m² correctly sits above villa); for one kommune use its exact series;
  // for a subset, median-of-kommune-medians. The dashed asking ref matches.
  const whole = S.munis.size >= S.meta.municipalities.length;
  const seriesFor = t => {
    const sa = whole && S.sold.seriesAll && S.sold.seriesAll[t];
    if (sa) return quarters.map(q => sa[q] != null ? sa[q] : null);
    return quarters.map(q => {
      const vals = [];
      for (const slug of sel) { const st = S.sold.series[slug] && S.sold.series[slug][t]; if (st && st[q] != null) vals.push(st[q]); }
      return vals.length ? Math.round(median(vals)) : null;
    });
  };
  const askMed = t => {
    if (whole) { const m = median(S.all.filter(r => r.t === t).map(r => r.m2p).filter(Boolean)); return m || null; }
    const per = [];
    for (const slug of sel) { const m = median(S.all.filter(r => r.t === t && r.muni === slug).map(r => r.m2p).filter(Boolean)); if (m) per.push(m); }
    return per.length ? median(per) : null;
  };
  const series = [], refs = [];
  if (S.type !== 'villa') { series.push({ name: 'Ejerlejlighed', color: cssVar('--condo'), values: seriesFor('condo') }); const a = askMed('condo'); if (a) refs.push({ value: Math.round(a), color: cssVar('--condo'), label: 'udbud nu' }); }
  if (S.type !== 'condo') { series.push({ name: 'Villa/hus', color: cssVar('--villa'), values: seriesFor('villa') }); const a = askMed('villa'); if (a) refs.push({ value: Math.round(a), color: cssVar('--villa'), label: 'udbud nu' }); }
  const N = quarters.length; let idxs;
  if (N <= 8) idxs = quarters.map((_, i) => i);
  else idxs = thinTicks(N, 7);
  lineChart(mount, quarters, series, { legend: true, yfmt: kc, tfmt: m2, refs, xticks: idxs.map(i => [i, quarters[i]]), empty: 'Ingen realiserede salgsdata for det valgte område.' });
  // The gap to "udbud nu" is usually bigger here than the Udbud-vs-realiseret
  // chart's overall 12-month figure suggests, and that's expected, not a bug:
  // tinglysning (deed registration) typically lags the agreed price by weeks
  // to months, so the newest quarter reflects deals struck earlier — in a
  // rising market it understates where prices are today. It's also the
  // *current*, still-filling quarter, so its median can still move as more
  // sales register.
  if (note) note.textContent = `Median realiseret kr/m² pr. kvartal — ${scopeName}. Stiplet linje = nuværende udbudsniveau. Seneste kvartal er endnu ikke afsluttet, og tinglysning sker typisk uger–måneder efter selve handlen — så gabet til "udbud nu" er normalt større end den generelle udbud/realiseret-forskel. Kilde: Boliga/tinglysning.`;
}

// Realised kr/m² by BBR build-decade (corridor-wide), condo vs. villa — shows
// how construction era maps to what homes actually fetch.
function renderSoldDecade() {
  const box = $('#chartSoldDecade'), note = $('#soldDecadeNote'); if (!box) return;
  box.innerHTML = '';
  const bd = S.sold && S.sold.byDecade;
  if (!bd || (!bd.condo && !bd.villa)) { box.append(el('div', { class: 'loading' }, 'Ingen data.')); if (note) note.textContent = ''; return; }
  const decades = [...new Set([...Object.keys(bd.condo || {}), ...Object.keys(bd.villa || {})].map(Number))].sort((a, b) => a - b);
  const xlab = decades.map(d => d + 'er');
  const series = [];
  if (S.type !== 'villa' && bd.condo) series.push({ name: 'Ejerlejlighed', color: cssVar('--condo'), values: decades.map(d => bd.condo[d] ?? null) });
  if (S.type !== 'condo' && bd.villa) series.push({ name: 'Villa/hus', color: cssVar('--villa'), values: decades.map(d => bd.villa[d] ?? null) });
  if (!series.length) { box.append(el('div', { class: 'loading' }, 'Ingen data for den valgte boligtype.')); if (note) note.textContent = ''; return; }
  // thin the decade labels so they don't overlap (show ~8 evenly, always incl. last)
  const dIdx = thinTicks(decades.length, 8);
  lineChart(box, xlab, series, { legend: true, yfmt: kc, tfmt: m2, xticks: dIdx.map(i => [i, xlab[i]]) });
  if (note) note.textContent = 'Median realiseret kr/m² efter opførelsesårti (BBR), hele korridoren, seneste 12 mdr. Kilde: Boliga/tinglysning + BBR.';
}

// rows: [{label, value, n?, color?}].  opt: yfmt (y-axis + on-column label),
// fmt (full value for tooltip), vlabel, tip(r) (custom tooltip), angle (x-label
// rotation; 0 = horizontal), W, H, padB, headroom, topLabels.
function vbars(mount, rows, opt = {}) {
  mount.innerHTML = '';
  if (!rows.length) { mount.append(el('div', { class: 'loading' }, opt.empty || 'Ingen data for det valgte filter.')); return; }
  const W = opt.W || 640, H = opt.H || 300;
  const padL = opt.padL ?? 40, padR = 12, padT = 16, padB = opt.padB ?? 74;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const max = Math.max(...rows.map(r => r.value)) * (opt.headroom ?? 1.08) || 1;
  const yfmt = opt.yfmt || num, fmt = opt.fmt || yfmt, angle = opt.angle ?? -40;
  const svg = svel('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' });
  for (let g = 0; g <= 4; g++) {
    const y = padT + plotH - g / 4 * plotH;
    svg.append(svel('line', { x1: padL, y1: y, x2: W - padR, y2: y, class: 'gridline' }));
    const t = svel('text', { x: padL - 6, y: y + 3, 'text-anchor': 'end', class: 'axis-txt' }); t.textContent = yfmt(max * g / 4); svg.append(t);
  }
  const bw = plotW / rows.length;
  rows.forEach((r, i) => {
    const h = r.value / max * plotH, x = padL + i * bw, y = padT + plotH - h;
    const g = svel('g');
    const dim = opt.selected != null && r.label !== opt.selected;
    g.append(svel('rect', { x: x + bw * .16, y, width: bw * .68, height: h, rx: 4, fill: r.color || typeColor(), opacity: dim ? 0.32 : 1 }));
    if (opt.topLabels !== false) { const vt = svel('text', { x: x + bw / 2, y: y - 5, 'text-anchor': 'middle', class: 'bar-val', opacity: dim ? 0.4 : 1 }); vt.textContent = yfmt(r.value); g.append(vt); }
    g.addEventListener('mousemove', e => showTip(opt.tip ? opt.tip(r) : `<div class="tt-title">${r.label}</div><div class="tt-row"><span>${opt.vlabel || 'Værdi'}</span><b>${fmt(r.value)}</b></div>${r.n != null ? `<div class="tt-row"><span>Antal boliger</span><b>${r.n}</b></div>` : ''}`, e.clientX, e.clientY));
    g.addEventListener('mouseleave', hideTip);
    if (opt.onBar) { g.style.cursor = 'pointer'; g.addEventListener('click', () => opt.onBar(r)); }
    svg.append(g);
    const lx = x + bw / 2, ly = H - padB + 15;
    const lt = svel('text', { x: lx, y: ly, class: 'axis-txt' }); lt.textContent = r.label;
    if (angle) { lt.setAttribute('text-anchor', 'end'); lt.setAttribute('transform', `rotate(${angle} ${lx} ${ly})`); }
    else lt.setAttribute('text-anchor', 'middle');
    svg.append(lt);
  });
  mount.append(svg);
}

// Vertical columns, one per kommune — the (long) names angled so all ~14 fit.
// Click a column to see that kommune's key numbers below the chart; click it
// again (or the ✕ in the stats box) to close it.
let muniSel = null;
let muniChartF = null;   // last filtered rows, so the ✕ close button can re-render without its own copy
function renderMuniChart(f) {
  muniChartF = f;
  const byM = new Map();
  f.forEach(r => { (byM.get(r.muni) || byM.set(r.muni, []).get(r.muni)).push(r.m2p); });
  const names = Object.fromEntries(S.meta.municipalities.map(m => [m.slug, m.name]));
  const slugOf = Object.fromEntries(S.meta.municipalities.map(m => [m.name, m.slug]));
  const rows = [...byM.entries()].map(([slug, arr]) => ({ label: names[slug] || slug, slug, value: Math.round(median(arr.filter(Boolean))), n: arr.length }))
    .filter(r => r.value).sort((a, b) => b.value - a.value);
  if (muniSel && !rows.some(r => r.slug === muniSel)) muniSel = null;   // drop if filtered out
  const selLabel = muniSel ? (names[muniSel] || muniSel) : null;
  vbars($('#chartMuni'), rows, {
    W: 760, padB: 82, angle: -40, yfmt: kc, fmt: m2, vlabel: 'Median pris/m²', selected: selLabel,
    onBar: r => { muniSel = (muniSel === r.slug) ? null : r.slug; renderMuniChart(f); renderMuniStats(); },
  });
  renderMuniStats();
}
function closeMuniStats() { muniSel = null; renderMuniChart(muniChartF); }
function renderMuniStats() {
  const box = $('#chartMuniStats'); if (!box) return;
  box.innerHTML = '';
  if (!muniSel) { box.append(el('p', { class: 'chart-note' }, 'Klik på en kommune for at se gennemsnit for pris, størrelse, liggetid m.m.')); return; }
  const name = (S.meta.municipalities.find(m => m.slug === muniSel) || {}).name || muniSel;
  const s = kmStats(muniSel);
  const sv = soldForMuni(muniSel);
  const soldGap = (sv && s.medM2) ? Math.round((sv.m2 / s.medM2 - 1) * 100) : null;   // realiseret ift. udbud (negativ når udbud er højere)
  const stat = (label, val) => el('div', { class: 'kstat' }, el('div', { class: 'kstat-v' }, val), el('div', { class: 'kstat-l' }, label));
  const wrap = el('div', { class: 'kstats' },
    stat('Antal til salg', num(s.n)),
    stat('Median pris', s.medPrice != null ? krM(s.medPrice) : '–'),
    stat('Median pris/m²', s.medM2 != null ? m2(s.medM2) : '–'),
    stat('Gns. størrelse', s.avgSize != null ? Math.round(s.avgSize) + ' m²' : '–'),
    stat('Median liggetid', s.medDays != null ? Math.round(s.medDays) + ' dage' : '–'),
    stat('Nær S-tog', s.nearPct != null ? s.nearPct + ' %' : '–'),
    stat('Prisnedsat', s.pctCut != null ? s.pctCut + ' %' : '–'),
    stat('S-togspræmie', s.premium != null ? (s.premium >= 0 ? '+' : '') + s.premium + ' %' : '–'),
    stat('Metropræmie', s.metroPremium != null ? (s.metroPremium >= 0 ? '+' : '') + s.metroPremium + ' %' : '–'),
    stat('Realiseret salg', sv ? m2(sv.m2) : '–'),
    stat('Udbud vs. salg', soldGap != null ? (soldGap >= 0 ? '+' : '') + soldGap + ' %' : '–'),
    stat('Realiseret str.', sv && sv.size ? Math.round(sv.size) + ' m²' : '–'),
    stat('Byggeår (solgt)', sv && sv.year ? String(sv.year) : '–'));
  const closeBtn = el('button', { type: 'button', class: 'kstats-close', 'aria-label': 'Luk' }, '✕ Luk');
  closeBtn.addEventListener('click', closeMuniStats);
  box.append(el('div', { class: 'kstats-head' }, el('b', {}, name), el('span', { class: 'src' }, ' · ' + (S.type === 'all' ? 'alle boligtyper' : S.type === 'condo' ? 'ejerlejligheder' : 'villaer')), closeBtn));
  box.append(wrap);
  // rooms + energy-label mix for the selected kommune
  const rows = S.all.filter(r => r.muni === muniSel && (S.type === 'all' || r.t === S.type));
  const ramp = seqRamp();
  const roomsE = [['1 vær', r => (r.r || 0) <= 1], ['2', r => r.r === 2], ['3', r => r.r === 3], ['4', r => r.r === 4], ['5+', r => (r.r || 0) >= 5]]
    .map((d, i) => ({ label: d[0], count: rows.filter(d[1]).length, color: ramp[i + 1] }));
  const eCol = ['#1a9e5f', '#5bbf4a', '#a7d234', '#f5c518', '#f39325', '#e5622d', '#d0342c'];
  const energyE = ['a', 'b', 'c', 'd', 'e', 'f', 'g']
    .map((l, i) => ({ label: l.toUpperCase(), count: rows.filter(r => String(r.e || '').toLowerCase().startsWith(l)).length, color: eCol[i] }));
  const blk = (title, entries) => el('div', { class: 'dist-block' }, el('div', { class: 'dist-title' }, title), distBar(entries));
  box.append(el('p', { class: 'chart-note' }, `Fordeling af værelsesantal og energimærke blandt boligerne til salg i ${name} lige nu — bredden af hvert farvet segment er dets andel; hold musen over et segment for tal.`));
  box.append(el('div', { class: 'dist-wrap' }, blk('Værelser', roomsE), blk('Energimærke', energyE)));
}
// segmented proportion bar for a small distribution
function distBar(entries) {
  const total = entries.reduce((a, e) => a + e.count, 0) || 1;
  const bar = el('div', { class: 'distbar' });
  entries.forEach(e => {
    if (!e.count) return;
    const pct = e.count / total;
    const seg = el('span', { class: 'distseg', style: `width:${(pct * 100).toFixed(1)}%;background:${e.color}`, title: `${e.label}: ${e.count} (${Math.round(pct * 100)} %)` });
    if (pct > 0.1) seg.textContent = e.label;
    bar.append(seg);
  });
  return bar;
}

function renderDistChart(f) {
  const buckets = [['0–500 m', 0, 500], ['500 m–1 km', 500, 1000], ['1–2 km', 1000, 2000], ['2–4 km', 2000, 4000], ['over 4 km', 4000, Infinity]];
  const rows = buckets.map(([label, lo, hi]) => {
    const arr = f.filter(r => r.sst >= lo && r.sst < hi).map(r => r.m2p).filter(Boolean);
    return { label, value: Math.round(median(arr) || 0), n: arr.length };
  }).filter(r => r.n);
  vbars($('#chartDist'), rows, { angle: 0, padB: 34, yfmt: kc, fmt: m2, vlabel: 'Median pris/m²' });
}

function renderDaysChart(f) {
  const days = f.map(r => r.d).filter(v => v != null);
  const mount = $('#chartDays');
  if (!days.length) { mount.innerHTML = ''; mount.append(el('div', { class: 'loading' }, 'Ingen data.')); return; }
  const edges = [0, 14, 30, 60, 90, 120, 180, 270, 365, Infinity];
  const labels = ['<2 uger', '2–4 uger', '1–2 mdr', '2–3 mdr', '3–4 mdr', '4–6 mdr', '6–9 mdr', '9–12 mdr', '>1 år'];
  const counts = new Array(labels.length).fill(0);
  days.forEach(d => { for (let i = 0; i < edges.length - 1; i++) if (d >= edges[i] && d < edges[i + 1]) { counts[i]++; break; } });
  const rows = labels.map((label, i) => ({ label, value: counts[i] }));
  vbars(mount, rows, {
    angle: -35, padB: 54, yfmt: v => Math.round(v),
    tip: r => `<div class="tt-title">${r.label}</div><div class="tt-row"><span>Boliger</span><b>${r.value}</b></div><div class="tt-row"><span>Andel</span><b>${Math.round(r.value / days.length * 100)} %</b></div>`,
  });
}

/* ===================== price by build year ===================== */
function renderYearChart(f) {
  const buckets = [['før 1900', -1e9, 1900], ['1900–39', 1900, 1940], ['1940–59', 1940, 1960],
    ['1960–79', 1960, 1980], ['1980–99', 1980, 2000], ['2000–09', 2000, 2010],
    ['2010–19', 2010, 2020], ['2020+', 2020, 1e9]];
  const rows = buckets.map(([label, lo, hi]) => {
    const arr = f.filter(r => r.y && r.y >= lo && r.y < hi).map(r => r.m2p).filter(Boolean);
    return { label, value: Math.round(median(arr) || 0), n: arr.length };
  }).filter(r => r.n >= 5);
  vbars($('#chartYear'), rows, { angle: -25, padB: 44, yfmt: kc, fmt: m2, vlabel: 'Median pris/m²' });
}

/* ===================== scatter: size vs kr/m² ===================== */
function renderScatter(f) {
  const mount = $('#chartScatter'); mount.innerHTML = '';
  const pts = f.filter(r => r.a > 0 && r.m2p > 0);
  if (pts.length < 5) { mount.append(el('div', { class: 'loading' }, 'For få boliger.')); return; }
  const W = 640, H = 280, padL = 54, padR = 16, padT = 14, padB = 40;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const areas = pts.map(r => r.a), m2s = pts.map(r => r.m2p);
  // trimmed range + a margin, so the cloud sits inside the plot and doesn't
  // pile up against the axes
  // data range (what we draw) vs. axis domain (a margin wider), so the cloud
  // sits inside the plot with clear space to every axis
  const xd0 = quantile(areas, .01), xd1 = Math.min(360, quantile(areas, .99));
  const yd0 = quantile(m2s, .02), yd1 = quantile(m2s, .98);
  const xp = (xd1 - xd0) * 0.08 || 1, yp = (yd1 - yd0) * 0.10 || 1;
  const xLo = Math.max(0, xd0 - xp), xHi = xd1 + xp, yLo = Math.max(0, yd0 - yp), yHi = yd1 + yp;
  const xMax = xHi;   // used by the per-type trend binning below
  const X = v => padL + (clamp(v, xLo, xHi) - xLo) / (xHi - xLo) * plotW;
  const Y = v => padT + plotH - (clamp(v, yLo, yHi) - yLo) / (yHi - yLo) * plotH;
  const svg = svel('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' });
  for (let g = 0; g <= 4; g++) {
    const yv = yLo + (yHi - yLo) * g / 4, y = Y(yv);
    svg.append(svel('line', { x1: padL, y1: y, x2: W - padR, y2: y, class: 'gridline' }));
    const t = svel('text', { x: padL - 6, y: y + 3, 'text-anchor': 'end', class: 'axis-txt' });
    t.textContent = Math.round(yv / 1000) + 'k'; svg.append(t);
  }
  for (let g = 0; g <= 4; g++) {
    const xv = xLo + (xHi - xLo) * g / 4, x = X(xv);
    const t = svel('text', { x, y: H - padB + 15, 'text-anchor': 'middle', class: 'axis-txt' });
    t.textContent = Math.round(xv) + ' m²'; svg.append(t);
  }
  // axis titles
  const xt = svel('text', { x: padL + plotW / 2, y: H - 2, 'text-anchor': 'middle', class: 'axis-title' });
  xt.textContent = 'Boligstørrelse (m²)'; svg.append(xt);
  const yt = svel('text', { x: 12, y: padT + plotH / 2, 'text-anchor': 'middle', class: 'axis-title',
    transform: `rotate(-90 12 ${padT + plotH / 2})` });
  yt.textContent = 'Pris pr. m²'; svg.append(yt);

  // draw only the in-range cloud; a few extreme homes are left off the scale so
  // they don't pile up as a line of dots on the axes
  const inRange = r => r.a >= xd0 && r.a <= xd1 && r.m2p >= yd0 && r.m2p <= yd1;
  const shown = pts.filter(inRange), hidden = pts.length - shown.length;
  const gDots = svel('g', {});
  shown.forEach(r => {
    const c = svel('circle', { cx: X(r.a).toFixed(1), cy: Y(r.m2p).toFixed(1), r: 2.2,
      fill: r.t === 'villa' ? cssVar('--villa') : cssVar('--condo'), opacity: .45 });
    c._r = r; gDots.append(c);
  });
  svg.append(gDots);
  gDots.addEventListener('mousemove', e => {
    const t = e.target; if (t.tagName !== 'circle' || !t._r) return; const r = t._r;
    showTip(`<div class="tt-title">${esc(r.adr)}</div><div class="tt-row"><span>${esc(r.city)}</span><b>${homeTypeLabel(r)}</b></div><div class="tt-row"><span>Størrelse</span><b>${r.a} m²</b></div><div class="tt-row"><span>Pris/m²</span><b>${m2(r.m2p)}</b></div><div class="tt-row"><span>Pris</span><b>${krM(r.p)}</b></div>`, e.clientX, e.clientY);
  }, true);
  gDots.addEventListener('mouseout', hideTip, true);

  // one median trend line per housing type (mixing them hides the real pattern)
  const pearson = (xs, ys) => {
    const n = xs.length; if (n < 3) return null;
    const mx = xs.reduce((a, b) => a + b, 0) / n, my = ys.reduce((a, b) => a + b, 0) / n;
    let cov = 0, vx = 0, vy = 0;
    for (let i = 0; i < n; i++) { const dx = xs[i] - mx, dy = ys[i] - my; cov += dx * dy; vx += dx * dx; vy += dy * dy; }
    return (vx && vy) ? cov / Math.sqrt(vx * vy) : null;
  };
  const BIN = 25, stats = [];
  ['condo', 'villa'].forEach(t => {
    const rows = pts.filter(r => r.t === t);
    if (rows.length < 25) return;
    const color = cssVar(t === 'villa' ? '--villa' : '--condo');
    const bins = new Map();
    rows.forEach(r => { const b = Math.floor(Math.min(r.a, xMax) / BIN) * BIN; (bins.get(b) || bins.set(b, []).get(b)).push(r.m2p); });
    const line = [...bins.entries()].filter(([, v]) => v.length >= 10).sort((a, b) => a[0] - b[0])
      .map(([b, v]) => [X(b + BIN / 2), Y(median(v))]);
    if (line.length > 1) {
      svg.append(svel('polyline', { points: line.map(p => p.join(',')).join(' '), fill: 'none',
        stroke: cssVar('--surface'), 'stroke-width': 4.5, opacity: .9, 'stroke-linejoin': 'round' }));
      svg.append(svel('polyline', { points: line.map(p => p.join(',')).join(' '), fill: 'none',
        stroke: color, 'stroke-width': 2.4, 'stroke-linejoin': 'round' }));
    }
    stats.push({ t, color, n: rows.length, r: pearson(rows.map(x => x.a), rows.map(x => x.m2p)) });
  });
  mount.append(svg);

  // legend — makes clear these are current listings split by housing type
  const lg = el('div', { class: 'chart-legend' });
  stats.forEach(s => lg.append(el('span', { class: 'legend-item' },
    el('span', { class: 'swatch', style: `background:${s.color}` }),
    `${s.t === 'villa' ? 'Villa' : 'Ejerlejlighed'} (${s.n.toLocaleString('da-DK')}) — linje = median pr. ${BIN} m²`)));
  mount.append(lg);

  // caption describing what the data actually shows, not what we expect it to
  const dir = r => r == null ? null : (r > 0.08 ? 'stiger' : r < -0.08 ? 'falder' : 'er nogenlunde flad');
  const bits = stats.filter(s => s.r != null).map(s =>
    `${s.t === 'villa' ? 'villaer' : 'ejerlejligheder'} ${dir(s.r)} (r = ${s.r.toFixed(2).replace('.', ',')})`);
  mount.append(el('p', { class: 'chart-note' },
    'Hver prik er en bolig til salg lige nu — ikke en tidsserie. '
    + (bits.length ? `I det valgte udsnit: ${bits.join(', ')}. ` : '')
    + (stats.length > 1
      ? 'Den samlede sky kan se flad ud, selvom hver boligtype for sig stiger: store boliger er oftere villaer, som har lavere m²-pris end lejligheder (sammensætningseffekt). '
      : '')
    + (hidden > 0 ? `${hidden.toLocaleString('da-DK')} af ${pts.length.toLocaleString('da-DK')} boliger vises ikke i skyen: dem uden for ${Math.round(xd0)}–${Math.round(xd1)} m² (yderste ~1 % i hver ende af størrelsen) eller uden for ${Math.round(yd0 / 1000)}k–${Math.round(yd1 / 1000)}k kr/m² (yderste ~2 % i hver ende af m²-prisen). De holdes uden for skalaen, så skyen ikke klumper mod akserne — men de tælles stadig med i medianlinjerne og i alle nøgletal ovenfor.` : '')));
}

/* ===== sold/removed: listings that vanished from boligsiden — not visible in the
   card grid at all, since #cards only ever renders the current active listings.
   (Price-outlier and price-cut rankings used to live here too, but both were
   just the current listings re-sorted — "fund"/"chg" in the Boliger sort
   dropdown already show the same homes with the same badges on the cards.) */
function renderPriceChanges(f) {
  const box = $('#priceChanges'); if (!box) return; box.innerHTML = '';
  const names = Object.fromEntries(S.meta.municipalities.map(m => [m.slug, m.name]));
  if (!S.trackerMap) { box.append(el('div', { class: 'loading' }, 'Henter historik…')); return; }
  const sel = new Set([...S.munis]);
  const items = [...S.trackerMap.values()]
    .filter(it => it.removed && (S.type === 'all' || it.t === S.type) && sel.has(it.muni))
    .sort((a, b) => a.removed < b.removed ? 1 : -1).slice(0, 12);
  if (!items.length) { box.append(el('div', { class: 'loading' }, 'Endnu ingen solgte/fjernede boliger registreret — bygges op fra i dag, efterhånden som annoncer forsvinder fra boligsiden.')); return; }
  items.forEach(it => {
    const lastP = it.events && it.events.length ? it.events[it.events.length - 1][1] : null;
    const a = el('a', { class: 'ol-row', href: it.url || '#', target: '_blank', rel: 'noopener' });
    a.append(el('span', { class: 'ol-z hi' }, 'fjernet'));
    a.append(el('span', { class: 'ol-main' }, el('b', {}, it.adr || '—'),
      el('small', {}, `${names[it.muni] || it.muni} · ${it.t === 'villa' ? 'villa' : 'ejerlejl.'}${it.a ? ` · ${it.a} m²` : ''}`)));
    a.append(el('span', { class: 'ol-num' }, el('b', {}, lastP ? krM(lastP) : '–'), el('small', {}, it.lastD != null ? it.lastD + ' dage' : '')));
    a.append(el('span', { class: 'ol-num' }, el('b', {}, fmtDay(it.removed)), el('small', {}, 'sidst set ' + fmtDay(it.lastSeen))));
    box.append(a);
  });
  box.append(el('p', { class: 'chart-note' }, 'Boliger der er forsvundet fra boligsiden (solgt eller trukket tilbage), nyeste først — med sidste udbudspris og observeret liggetid.'));
}

/* ===================== line chart (shared: DST index + trend) ===================== */
function lineChart(mount, xLabels, series, opt = {}) {
  mount.innerHTML = '';
  const pts = xLabels.length;
  if (!pts || !series.some(s => s.values.some(v => v != null))) { mount.append(el('div', { class: 'loading' }, opt.empty || 'Ingen data endnu.')); return; }
  const W = 680, H = 260, padL = 46, padR = 14, padT = 12, padB = 30, plotW = W - padL - padR, plotH = H - padT - padB;
  const all = series.flatMap(s => s.values).concat((opt.bands || []).flatMap(b => [...b.lo, ...b.hi]))
    .concat((opt.refs || []).map(r => r.value)).filter(v => v != null);
  let lo = Math.min(...all), hi = Math.max(...all);
  if (opt.zeroBase) lo = Math.min(lo, 0);
  const span = (hi - lo) || 1; lo -= span * .06; hi += span * .06;
  const X = i => padL + (pts === 1 ? plotW / 2 : i / (pts - 1) * plotW);
  const Y = v => padT + plotH - (v - lo) / (hi - lo) * plotH;
  const svg = svel('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' });
  for (let g = 0; g <= 4; g++) { const yv = lo + (hi - lo) * g / 4, y = Y(yv); svg.append(svel('line', { x1: padL, y1: y, x2: W - padR, y2: y, class: 'gridline' })); const t = svel('text', { x: padL - 6, y: y + 3, 'text-anchor': 'end', class: 'axis-txt' }); t.textContent = opt.yfmt ? opt.yfmt(yv) : Math.round(yv); svg.append(t); }
  // x ticks
  (opt.xticks || []).forEach(([i, lab]) => {
    const x = X(i);
    svg.append(svel('line', { x1: x, y1: padT, x2: x, y2: padT + plotH, class: 'gridline' }));
    const anchor = x < padL + 14 ? 'start' : x > W - padR - 14 ? 'end' : 'middle';
    const t = svel('text', { x, y: H - padB + 15, 'text-anchor': anchor, class: 'axis-txt' }); t.textContent = lab; svg.append(t);
  });
  // shaded quartile bands (behind the lines), broken across any gaps
  (opt.bands || []).forEach(b => {
    let seg = [];
    const flush = () => {
      if (seg.length > 1) {
        const top = seg.map(i => `${X(i).toFixed(1)} ${Y(b.hi[i]).toFixed(1)}`);
        const bot = seg.slice().reverse().map(i => `${X(i).toFixed(1)} ${Y(b.lo[i]).toFixed(1)}`);
        svg.append(svel('path', { d: 'M' + top.join(' L') + ' L' + bot.join(' L') + ' Z', fill: b.color, opacity: 0.13, stroke: 'none' }));
      }
      seg = [];
    };
    for (let i = 0; i < pts; i++) { if (b.lo[i] != null && b.hi[i] != null) seg.push(i); else flush(); }
    flush();
  });
  // vertical context markers (e.g. "tracked area changed here") — behind the
  // data lines, since they're a backdrop annotation, not a data series
  (opt.vmarks || []).forEach(vm => {
    if (vm.index == null || vm.index < 0 || vm.index >= pts) return;
    const x = X(vm.index);
    svg.append(svel('line', { x1: x, y1: padT, x2: x, y2: padT + plotH, stroke: vm.color || '#888', 'stroke-width': 1.3, 'stroke-dasharray': '4 3', opacity: 0.7 }));
    if (vm.label) {
      const t = svel('text', { x: x + 4, y: padT + 10, 'text-anchor': 'start', class: 'axis-txt' });
      t.textContent = vm.label; if (vm.color) t.setAttribute('fill', vm.color);
      svg.append(t);
    }
  });
  series.forEach(s => {
    let d = '', started = false;
    s.values.forEach((v, i) => { if (v == null) { started = false; return; } d += (started ? ' L' : ' M') + X(i).toFixed(1) + ' ' + Y(v).toFixed(1); started = true; });
    svg.append(svel('path', { d: d.trim(), fill: 'none', stroke: s.color, 'stroke-width': 2.4, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    if (pts === 1) { const i = 0, v = s.values[0]; if (v != null) svg.append(svel('circle', { cx: X(i), cy: Y(v), r: 4, fill: s.color })); }
  });
  // horizontal reference lines (e.g. current asking level vs. the realised trend)
  (opt.refs || []).forEach(rf => {
    if (rf.value == null) return;
    const y = Y(rf.value);
    svg.append(svel('line', { x1: padL, y1: y, x2: W - padR, y2: y, stroke: rf.color, 'stroke-width': 1.5, 'stroke-dasharray': '5 4', opacity: 0.8 }));
    if (rf.label) { const t = svel('text', { x: W - padR - 2, y: y - 4, 'text-anchor': 'end', class: 'axis-txt' }); t.textContent = rf.label; t.setAttribute('fill', rf.color); svg.append(t); }
  });
  // hover crosshair
  const cross = svel('line', { y1: padT, y2: padT + plotH, class: 'crosshair', 'stroke-dasharray': '3 3' }); cross.style.display = 'none'; svg.append(cross);
  const hit = svel('rect', { x: padL, y: padT, width: plotW, height: plotH, fill: 'transparent' });
  hit.addEventListener('mousemove', e => {
    const r = svg.getBoundingClientRect(); const px = (e.clientX - r.left) / r.width * W;
    let i = Math.round((px - padL) / plotW * (pts - 1)); i = Math.max(0, Math.min(pts - 1, i));
    cross.setAttribute('x1', X(i)); cross.setAttribute('x2', X(i)); cross.style.display = '';
    const rows = series.map(s => s.values[i] != null ? `<div class="tt-row"><span><i class="dot" style="background:${s.color}"></i>${esc(s.name)}</span><b>${opt.tfmt ? opt.tfmt(s.values[i]) : s.values[i]}</b></div>` : '').join('');
    showTip(`<div class="tt-title">${xLabels[i]}</div>${rows}`, e.clientX, e.clientY);
  });
  hit.addEventListener('mouseleave', () => { cross.style.display = 'none'; hideTip(); });
  svg.append(hit);
  mount.append(svg);
  if (opt.legend) {
    const lg = el('div', { class: 'chart-legend' });
    series.forEach(s => lg.append(el('span', { class: 'legend-item' }, el('span', { class: 'swatch', style: `background:${s.color}` }), s.name)));
    mount.append(lg);
  }
}

// DST landsdele → the corridor municipalities they contain (for anchoring the
// index to today's real kr/m²). 084/000 aren't shown (can't anchor to our data).
const DST_LANDSDEL_MUNIS = {
  '01': ['koebenhavn', 'frederiksberg'],
  '02': ['gentofte', 'lyngby-taarbaek', 'gladsaxe', 'herlev', 'ballerup'],
  '03': ['rudersdal', 'furesoe', 'alleroed', 'hilleroed', 'hoersholm', 'egedal', 'fredensborg'],
};
function currentKrM2(areaId, type) {
  const set = new Set(DST_LANDSDEL_MUNIS[areaId] || []);
  return median(S.all.filter(r => r.t === type && set.has(r.muni)).map(r => r.m2p).filter(Boolean));
}
// When the kommune selection sits within a single DST landsdel, point the
// price-development chart's "Område" at it (Hillerød → Nordsjælland, etc.).
function autoFollowDstArea() {
  if (!S.index) return;
  const lds = new Set();
  S.munis.forEach(slug => {
    for (const [ld, arr] of Object.entries(DST_LANDSDEL_MUNIS)) if (arr.includes(slug)) lds.add(ld);
  });
  if (lds.size === 1) {
    const ld = [...lds][0];
    if (S.dstArea !== ld) { S.dstArea = ld; const sel = $('#dstArea'); if (sel) sel.value = ld; }
  }
}
// When exactly two kommuner are selected, default the compare card to those two.
function autoFollowCompare() {
  if (S.munis.size !== 2) return;
  const [a, b] = [...S.munis];
  S.cmpA = a; S.cmpB = b;
  const sa = $('#cmpA'), sb = $('#cmpB'); if (sa) sa.value = a; if (sb) sb.value = b;
}
function renderIndexChart() {
  const mount = $('#chartIndex');
  if (S.indexMode === 'real' && S.bvc) return renderRealIndexChart(mount);
  if (!S.index) { mount.innerHTML = ''; return; }
  const q = S.index.quarters, area = S.dstArea;
  const xticks = [];
  q.forEach((qq, i) => { if (qq.endsWith('K1') && (+qq.slice(0, 4)) % 4 === 0) xticks.push([i, qq.slice(0, 4)]); });
  const lastIdx = arr => { for (let i = arr.length - 1; i >= 0; i--) if (arr[i] != null) return arr[i]; return null; };
  // convert the DST index into estimated kr/m², anchored so the latest quarter
  // equals today's actual median kr/m² for that landsdel + type.
  const toKrM2 = (cat, type) => {
    const arr = S.index.series[area + '|' + cat] || [];
    const base = lastIdx(arr), anchor = currentKrM2(area, type);
    if (!base || !anchor) return arr.map(() => null);
    return arr.map(v => v == null ? null : Math.round(v / base * anchor));
  };
  const series = [];
  if (S.type !== 'villa') series.push({ name: 'Ejerlejlighed', color: cssVar('--condo'), values: toKrM2('condo', 'condo') });
  if (S.type !== 'condo') series.push({ name: 'Villa/hus', color: cssVar('--villa'), values: toKrM2('villa', 'villa') });
  lineChart(mount, q, series, { xticks, legend: true, yfmt: m2short, tfmt: m2 });
  const areaName = (S.index.areas.find(a => a.id === area) || {}).name || '';
  mount.append(el('p', { class: 'chart-note' },
    `Estimeret kr/m² for ${areaName}: Danmarks Statistiks kvartalsvise prisindeks (EJ56) skaleret, så seneste kvartal svarer til det aktuelle medianniveau. Viser prisernes bevægelse siden 1992, ikke faktiske historiske udbudspriser.`));
}
const m2short = v => v == null ? '–' : Math.round(v / 1000) + 'k';

// Long real (inflation-adjusted) prices, Boligøkonomisk Videncenter.
// Houses run from 1938, condos from 1973 — rebased to 2000 = 100 so the two are
// directly comparable on one axis.
function renderRealIndexChart(mount) {
  const b = S.bvc, hy = b.houses.years, cy = b.condos.years, BASE = 2000;
  const rebase = (vals, yrs) => {
    const i = yrs.indexOf(BASE), f = (i >= 0 && vals[i]) ? 100 / vals[i] : 1;
    return vals.map(v => v == null ? null : Math.round(v * f * 10) / 10);
  };
  const houses = rebase(b.houses.kbhfrb, hy);
  const condosR = rebase(b.condos.kbhfrb, cy);
  const condos = hy.map(y => { const i = cy.indexOf(y); return i < 0 ? null : condosR[i]; });
  const series = [];
  if (S.type !== 'condo') series.push({ name: 'Villa/hus (realt)', color: cssVar('--villa'), values: houses });
  if (S.type !== 'villa') series.push({ name: 'Ejerlejlighed (realt)', color: cssVar('--condo'), values: condos });
  const xticks = [];
  hy.forEach((y, i) => { if (y % 10 === 0) xticks.push([i, String(y)]); });
  lineChart(mount, hy.map(String), series, {
    xticks, legend: true, yfmt: v => Math.round(v), tfmt: v => v == null ? '–' : Math.round(v),
  });
  mount.append(el('p', { class: 'chart-note' },
    `Reale (inflationskorrigerede) boligpriser i København + Frederiksberg, indekseret så ${BASE} = 100. `
    + 'Huse måles fra 1938, ejerlejligheder fra 1973. Viser prisernes købekraft-korrigerede udvikling — '
    + 'toppen før finanskrisen og faldet efter er tydelige. Kilde: Boligøkonomisk Videncenter.'));
}

function renderTrendChart() {
  const mount = $('#chartTrend'); const hist = (S.history && S.history.series) || [];
  const metric = $('#trendMetric').value;
  const scope = S.munis.size === 1 ? [...S.munis][0] : 'all';
  const scopeName = scope === 'all' ? 'hele korridoren' : (S.meta.municipalities.find(m => m.slug === scope) || {}).name;
  const dates = [...new Set(hist.filter(r => r.scope === scope).map(r => r.date))].sort();
  $('#trendSrc').textContent = '· ' + scopeName + (dates.length < 2 ? ' · bygges op fra ' + (dates[0] || '') : ' · vores daglige målinger');
  const rowOf = (t, d) => hist.find(r => r.scope === scope && r.type === t && r.date === d);
  const pick = (t, d) => {
    const row = rowOf(t, d); if (!row) return null;
    // "premium" = how much dearer, per m², homes near an S-train are vs. farther out
    if (metric === 'premium') return (row.medM2Near && row.medM2Far) ? Math.round((row.medM2Near / row.medM2Far - 1) * 100) : null;
    return row[metric] != null ? row[metric] : null;
  };
  const bandFor = (t) => ({
    color: t === 'villa' ? cssVar('--villa') : cssVar('--condo'),
    lo: dates.map(d => { const r = rowOf(t, d); return r && r.q1M2 != null ? r.q1M2 : null; }),
    hi: dates.map(d => { const r = rowOf(t, d); return r && r.q3M2 != null ? r.q3M2 : null; }),
  });
  const series = [], bands = [];
  if (S.type !== 'villa') { series.push({ name: 'Ejerlejlighed', color: cssVar('--condo'), values: dates.map(d => pick('condo', d)) }); if (metric === 'medM2') bands.push(bandFor('condo')); }
  if (S.type !== 'condo') { series.push({ name: 'Villa/hus', color: cssVar('--villa'), values: dates.map(d => pick('villa', d)) }); if (metric === 'medM2') bands.push(bandFor('villa')); }
  const fmt = metric === 'premium' ? (v => (v >= 0 ? '+' : '') + Math.round(v) + ' %') : metric === 'medM2' ? m2 : metric === 'medPrice' ? krM : metric === 'pctCut' ? (v => Math.round(v) + ' %') : metric === 'medDays' ? (v => Math.round(v) + ' dage') : num;
  // compact y-axis labels so they don't get clipped (tooltip keeps the full value)
  const yfmt = metric === 'medM2' ? kc : metric === 'medPrice' ? (v => (v / 1e6).toLocaleString('da-DK', { maximumFractionDigits: 1 }) + ' mio.') : fmt;
  // date labels: short when the range is within a year, else include the year
  const spanDays = dates.length > 1 ? (new Date(dates[dates.length - 1]) - new Date(dates[0])) / 864e5 : 0;
  const dfmt = spanDays > 330
    ? d => new Date(d).toLocaleDateString('da-DK', { month: 'short', year: '2-digit' })
    : d => new Date(d).toLocaleDateString('da-DK', { day: 'numeric', month: 'short' });
  const xlab = dates.map(dfmt);
  // show every date when there are few, otherwise ~6 evenly spaced (always incl. last)
  const N = dates.length;
  let idxs;
  if (N <= 8) idxs = dates.map((_, i) => i);
  else idxs = thinTicks(N, 7);
  const note = dates.length < 2 ? 'Historikken bygges op fra i dag — kom tilbage om nogle dage for at se udviklingen i liggetid og prisnedsættelser.' : '';
  // The tracked area grew from the original S-tog corridor to hele Region
  // Hovedstaden on this date — a one-day jump in coverage (+275 boliger,
  // -3.8 % median kr/m² the same day) that looks like a market move in every
  // metric here but isn't one. Mark it so it doesn't get misread as a price drop.
  const REGION_EXPANSION_DATE = '2026-08-02';
  const expIdx = dates.indexOf(REGION_EXPANSION_DATE);
  const vmarks = expIdx >= 0 ? [{ index: expIdx, label: 'udvidet til hele Region H.', color: cssVar('--muted') }] : [];
  lineChart(mount, xlab, series, { legend: true, yfmt, tfmt: fmt, empty: note, bands, xticks: idxs.map(i => [i, xlab[i]]), vmarks });
  if (metric === 'medM2' && dates.length >= 2) mount.append(el('p', { class: 'chart-note' }, 'Skygget felt = midterste 50 % (kvartiler). Linjen er medianen.'));
  if (note && dates.length === 1) mount.append(el('p', { class: 'chart-note' }, note));
  if (expIdx >= 0) mount.append(el('p', { class: 'chart-note' }, 'Den stiplede grå linje markerer, hvor det sporede område blev udvidet fra S-togskorridoren til hele Region Hovedstaden — spring i tallene omkring den dato afspejler flere/andre kommuner, ikke et reelt markedsskift.'));
}

/* ===================== compare two kommuner ===================== */
function kmStats(slug) {
  const rows = S.all.filter(r => r.muni === slug && (S.type === 'all' || r.t === S.type));
  const m2 = rows.map(r => r.m2p).filter(Boolean), pr = rows.map(r => r.p).filter(Boolean), dd = rows.map(r => r.d).filter(v => v != null);
  const areas = rows.map(r => r.a).filter(Boolean);
  const near = rows.filter(r => r.near).map(r => r.m2p).filter(Boolean), far = rows.filter(r => !r.near).map(r => r.m2p).filter(Boolean);
  const mNear = rows.filter(r => nearMetro(r)).map(r => r.m2p).filter(Boolean), mFar = rows.filter(r => r.mst != null && !nearMetro(r)).map(r => r.m2p).filter(Boolean);
  const cuts = rows.filter(r => r.chg < 0).length, nearN = rows.filter(r => r.near).length;
  return {
    n: rows.length,
    medPrice: pr.length ? median(pr) : null,
    medM2: m2.length ? median(m2) : null,
    medDays: dd.length ? median(dd) : null,
    avgSize: areas.length ? areas.reduce((a, b) => a + b, 0) / areas.length : null,
    nearPct: rows.length ? Math.round(nearN / rows.length * 100) : null,
    pctCut: rows.length ? Math.round(cuts / rows.length * 100) : null,
    premium: (near.length && far.length) ? Math.round((median(near) / median(far) - 1) * 100) : null,
    // needs enough homes on both sides of the metro line to be meaningful — else null
    metroPremium: (mNear.length >= 8 && mFar.length >= 8) ? Math.round((median(mNear) / median(mFar) - 1) * 100) : null,
  };
}
function renderCompare() {
  const box = $('#cmpTable'); if (!box || !S.cmpA || !S.cmpB) return;
  const names = Object.fromEntries(S.meta.municipalities.map(m => [m.slug, m.name]));
  const A = kmStats(S.cmpA), B = kmStats(S.cmpB);
  const pct = v => v == null ? '–' : (v >= 0 ? '+' : '') + v + ' %';
  const rows = [
    ['Antal til salg', A.n, B.n, num, 0],
    ['Median pris', A.medPrice, B.medPrice, krM, -1],
    ['Median pris/m²', A.medM2, B.medM2, m2, -1],
    ['Median liggetid', A.medDays, B.medDays, v => Math.round(v) + ' dage', -1],
    ['Prisnedsættelser', A.pctCut, B.pctCut, v => v == null ? '–' : v + ' %', 0],
    ['S-togspræmie', A.premium, B.premium, pct, 0],
    ['Metropræmie', A.metroPremium, B.metroPremium, pct, 0],
  ];
  const t = el('table', { class: 'cmp-table' });
  t.append(el('thead', {}, el('tr', {}, el('th', {}, ''), el('th', {}, names[S.cmpA] || S.cmpA), el('th', {}, names[S.cmpB] || S.cmpB))));
  const tb = el('tbody');
  rows.forEach(([label, a, b, fmt]) => {
    tb.append(el('tr', {},
      el('td', { class: 'cmp-l' }, label),
      el('td', {}, a == null ? '–' : fmt(a)),
      el('td', {}, b == null ? '–' : fmt(b))));
  });
  t.append(tb); box.innerHTML = ''; box.append(t);
}

/* ===================== colour scales ===================== */
function seqRamp() { return ['--seq-1', '--seq-2', '--seq-3', '--seq-4', '--seq-5', '--seq-6'].map(cssVar); }
function makeScale(vals, invert) {
  const lo = quantile(vals, .05), hi = quantile(vals, .95), ramp = seqRamp();
  return v => { if (v == null) return cssVar('--muted'); let t = (v - lo) / (hi - lo || 1); t = Math.max(0, Math.min(1, t)); if (invert) t = 1 - t; return ramp[Math.min(ramp.length - 1, Math.floor(t * ramp.length))]; };
}

/* ===================== MAP (Leaflet + tiles) ===================== */
const MAP = { map: null, tiles: null, L: {}, renderer: null, inited: false };
const LINE_COLORS = () => ({ central: cssVar('--ink-2'), hilleroed: cssVar('--condo'), klampenborg: '#12a06f', farum: '#7a5cff', frederikssund: '#e08a00', kystbanen: cssVar('--muted') });
const BIG_STATIONS = new Set(['København H', 'Hellerup', 'Nørreport', 'Lyngby', 'Holte', 'Birkerød', 'Allerød', 'Hillerød', 'Farum', 'Værløse', 'Ballerup', 'Herlev', 'Klampenborg', 'Charlottenlund', 'Gentofte', 'Bagsværd', 'Rungsted Kyst', 'Nivå', 'Ordrup', 'Buddinge', 'Svanemøllen', 'Virum']);
// S-tog line colours (issue #6) — one per real line ref from meta.railGeom.
// Sampled directly from DSB's official line diagram, not approximated.
const STOG_COLORS = { A: '#30b4e6', B: '#50ac34', Bx: '#b1cf75', C: '#f19205', E: '#897db5', F: '#f9c30d', H: '#e44215' };
const STOG_ORDER = ['A', 'B', 'Bx', 'C', 'E', 'F', 'H'];
const STOG_OFFSET_M = 12;   // sideways spacing so lines sharing track show as parallel strands
// Amenity overlay (issue #7) — data/poi.json. childcare folds into daginstitution.
const POI_STYLE = {
  supermarket:  { c: '#2e7d32', label: 'Indkøb' },
  school:       { c: '#1565c0', label: 'Skole' },
  kindergarten: { c: '#ad1457', label: 'Daginstitution' },
  childcare:    { c: '#ad1457', label: 'Daginstitution' },
};
const POI_ZOOM = 13;   // amenities are dense — only label/draw them zoomed in
const LOKAL_COLOR = '#8a6d3b';   // local light rail (Nærumbanen / Gribskovbanen)

// Rail/transit geometry comes from real OSM route relations, which run the
// line's full physical length — e.g. S-tog A continues to Køge, H to
// Frederikssund's own outskirts — well past the 28 kommuner this app
// actually tracks. Drawing that full length implies data coverage the app
// doesn't have, so rail/transit lines and stations are capped to the
// tracked kommunes' combined outline (S.geo, already loaded for the
// choropleth boundaries).
let _regionIndex = null;
function regionIndex() {
  if (!_regionIndex) _regionIndex = (S.meta.municipalities || []).map(m => S.geo[m.slug]).filter(Boolean);
  return _regionIndex;
}
function pointInRing(lat, lon, ring) {   // ring: [[lon,lat], …]; standard ray-casting
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
    if (((yi > lat) !== (yj > lat)) && (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}
function inTrackedRegion(lat, lon) {
  return regionIndex().some(g => {
    const [minlon, minlat, maxlon, maxlat] = g.bbox;
    if (lon < minlon || lon > maxlon || lat < minlat || lat > maxlat) return false;
    return g.rings.some(ring => pointInRing(lat, lon, ring));
  });
}
// splits a polyline into the sub-runs that fall inside the tracked region, so
// a tail running off into untracked territory is dropped rather than drawn
function clipToRegion(pts) {
  const runs = []; let cur = [];
  for (const p of pts) {
    if (inTrackedRegion(p[0], p[1])) cur.push(p);
    else { if (cur.length > 1) runs.push(cur); cur = []; }
  }
  if (cur.length > 1) runs.push(cur);
  return runs;
}

function isDark() { const t = document.documentElement.getAttribute('data-theme'); return t ? t === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches; }
function tileUrl() {
  return isDark()
    ? 'https://{s}.basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}{r}.png'
    : 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png';
}
function regionBounds() {
  let a = 1e9, b = 1e9, c = -1e9, d = -1e9;
  Object.values(S.geo || {}).forEach(g => { const x = g.bbox; a = Math.min(a, x[1]); b = Math.min(b, x[0]); c = Math.max(c, x[3]); d = Math.max(d, x[2]); });
  if (a === 1e9) { S.all.forEach(r => { a = Math.min(a, r.lat); b = Math.min(b, r.lon); c = Math.max(c, r.lat); d = Math.max(d, r.lon); }); }
  return [[a, b], [c, d]];
}
// Default view: fit to where the homes actually are (the kommune polygons sprawl
// into Øresund and the western fjords, which leaves the corridor looking tiny).
// Trim the 1.5 % tails so a lone outlier can't zoom the whole map out.
function homeBounds() {
  const lats = S.all.map(r => r.lat).filter(Boolean).sort((x, y) => x - y);
  const lons = S.all.map(r => r.lon).filter(Boolean).sort((x, y) => x - y);
  if (lats.length < 20) return regionBounds();
  const q = (arr, p) => arr[Math.min(arr.length - 1, Math.max(0, Math.floor(arr.length * p)))];
  return [[q(lats, 0.015), q(lons, 0.015)], [q(lats, 0.985), q(lons, 0.985)]];
}

function initMap() {
  // zoomSnap < 1 lets fitBounds settle on a fractional zoom that fills the frame,
  // instead of dropping a whole level when the region is a hair too tall/wide
  const map = L.map('map', { preferCanvas: true, zoomControl: true, minZoom: 8, maxZoom: 18, zoomSnap: 0.25, zoomDelta: 0.5, doubleClickZoom: true, scrollWheelZoom: true });
  MAP.map = map;
  MAP.renderer = L.canvas({ padding: 0.4 });
  // Metro/letbane renders into a dedicated low pane so it sits *under* the S-tog
  // lines and the home dots instead of the bright Cityring painting over them.
  map.createPane('transitPane');
  map.getPane('transitPane').style.zIndex = 350;   // above tiles (200), below overlay (400)
  MAP.transitRenderer = L.canvas({ pane: 'transitPane', padding: 0.4 });
  L.control.scale({ imperial: false }).addTo(map);
  setTiles();
  MAP.L.boundaries = L.layerGroup().addTo(map);
  MAP.L.rail = L.layerGroup().addTo(map);
  MAP.L.listings = L.layerGroup().addTo(map);
  MAP.L.labels = L.layerGroup().addTo(map);
  MAP.L.stations = L.layerGroup().addTo(map);
  MAP.L.stationLabels = L.layerGroup().addTo(map);
  MAP.L.transit = L.layerGroup().addTo(map);
  MAP.L.transitLabels = L.layerGroup().addTo(map);
  MAP.L.pois = L.layerGroup().addTo(map);
  MAP.L.geo = L.layerGroup().addTo(map);
  map.fitBounds(homeBounds(), { padding: [6, 6] });
  drawRail(); drawStations(); applyRailVisibility();
  drawTransit(); applyTransitVisibility();
  drawPois();
  map.on('mouseout', hideTip);
  map.on('zoomend', () => { resizeDots(); drawPriceLabels(); drawTransitLabels(); drawStationLabels(); drawPois(); });
  map.on('moveend', drawPriceLabels);

  // "reset view" control next to the zoom buttons
  const Reset = L.Control.extend({
    options: { position: 'topleft' },
    onAdd() {
      const a = L.DomUtil.create('a', 'leaflet-bar map-reset');
      a.href = '#'; a.title = 'Nulstil kortet'; a.innerHTML = '⤢';
      L.DomEvent.on(a, 'click', L.DomEvent.stop).on(a, 'click', () => fitAll());
      return a;
    },
  });
  map.addControl(new Reset());
  window.__MAP = MAP;   // handy for debugging from the console
  setTimeout(() => map.invalidateSize(), 200);
  addEventListener('resize', () => map.invalidateSize());
  MAP.inited = true;
}
function setTiles() {
  if (MAP.tiles) MAP.map.removeLayer(MAP.tiles);
  MAP.tiles = L.tileLayer(tileUrl(), {
    subdomains: 'abcd', maxZoom: 19, detectRetina: true,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>, &copy; <a href="https://carto.com/attributions">CARTO</a>',
  });
  MAP.tiles.addTo(MAP.map); MAP.tiles.bringToBack();
}

// Show or hide the S-train / Kystbane lines and stations together.
function applyRailVisibility() {
  if (!MAP.map) return;
  [MAP.L.rail, MAP.L.stations, MAP.L.stationLabels].forEach(layer => {
    if (!layer) return;
    if (S.showRail) { if (!MAP.map.hasLayer(layer)) layer.addTo(MAP.map); }
    else if (MAP.map.hasLayer(layer)) MAP.map.removeLayer(layer);
  });
}
// Metro + Ring 3 letbane overlay (data/meta.json → transit). The raw OSM data is
// a soup of ~1000 way-fragments: many metro ways carry no colour tag (they'd
// default to red and bleed over the yellow/green lines) and the coloured lines
// share track (M1+M2 on Vanløse–Christianshavn, M3+M4 on the shared stretch),
// so overlapping segments overpaint each other. We fix both here:
//   • canonical colour per line ref (M1 green, M2 yellow, M3 red, M4 blue),
//   • unknown/untagged ways drawn thin & grey *underneath* (never red),
//   • each line nudged sideways by a small perpendicular offset so shared
//     corridors render as parallel lines instead of one colour hiding another.
const METRO_COLORS = { metro: '#6b7280', letbane: '#00a3c7' };
const METRO_REFS = { M1: '#007756', M2: '#f5c400', M3: '#e2231a', M4: '#0098d4' };
const METRO_REF_ORDER = ['M1', 'M2', 'M3', 'M4'];
// OSM tags the same line inconsistently — some ways carry ref=M3, others only a
// colour. Map the colours we see in the data back to a ref so every fragment of a
// line gets the same colour AND the same sideways offset (otherwise one line
// draws as two parallel strands).
const METRO_COL_REF = { '#007756': 'M1', '#ffcc00': 'M2', '#f5c400': 'M2', '#ff0a0a': 'M3', '#e2231a': 'M3', '#e2221a': 'M3', '#009cd3': 'M4', '#0098d4': 'M4' };
const METRO_OFFSET_M = 16;   // sideways spacing between parallel metro lines (metres)
// shift a run of [lat,lon] points sideways (perpendicular to the segment's
// overall bearing) by `metres`, so co-located lines separate on the map
function offsetLatLngs(pts, metres) {
  if (!metres || pts.length < 2) return pts;
  const [aLat, aLon] = pts[0], [bLat, bLon] = pts[pts.length - 1];
  const latR = (aLat + bLat) / 2 * Math.PI / 180;
  const mPerLon = 111320 * Math.cos(latR) || 1, mPerLat = 110540;
  const dx = (bLon - aLon) * mPerLon, dy = (bLat - aLat) * mPerLat;
  const len = Math.hypot(dx, dy) || 1;
  const dLat = (-dx / len * metres) / mPerLat;     // perpendicular = rotate 90°
  const dLon = (dy / len * metres) / mPerLon;
  return pts.map(([la, lo]) => [la + dLat, lo + dLon]);
}
function drawTransit() {
  if (!MAP.L.transit) return;
  MAP.L.transit.clearLayers();
  const tr = S.meta && S.meta.transit; if (!tr) return;
  const mid = (METRO_REF_ORDER.length - 1) / 2;
  // transit.lines is now one entry per metro line (M1–M4) from route relations,
  // already deduped to a single track — draw one polyline per line, nudged
  // sideways by ref so lines sharing a tunnel render as parallel strands.
  (tr.lines || []).forEach(ln => {
    if (ln.mode === 'lokal') {   // Nærumbanen / Gribskovbanen — dashed, own colour
      (ln.segs || []).forEach(seg => clipToRegion(seg).forEach(run =>
        L.polyline(run, { renderer: MAP.transitRenderer,
          color: LOKAL_COLOR, weight: 1.3, opacity: 0.7, dashArray: '2 5', lineCap: 'round', lineJoin: 'round' }).addTo(MAP.L.transit)));
      return;
    }
    const refCol = METRO_REFS[ln.ref];
    const tagCol = ln.colour && /^#?[0-9a-fA-F]{6}$/.test(ln.colour) ? (ln.colour[0] === '#' ? ln.colour : '#' + ln.colour) : null;
    const c = refCol || tagCol || METRO_COLORS.metro;
    const idx = METRO_REF_ORDER.indexOf(ln.ref);
    const off = idx >= 0 ? (idx - mid) * METRO_OFFSET_M : 0;
    (ln.segs || []).forEach(seg => clipToRegion(offsetLatLngs(seg, off)).forEach(run =>
      L.polyline(run, { renderer: MAP.transitRenderer,
        color: c, weight: 1.4, opacity: 0.7, lineCap: 'round', lineJoin: 'round' }).addTo(MAP.L.transit)));
  });
  (tr.stations || []).forEach(st => {
    if (!inTrackedRegion(st.lat, st.lon)) return;
    L.circleMarker([st.lat, st.lon], { renderer: MAP.transitRenderer, radius: 3, color: '#4b5563', weight: 1.6, fillColor: cssVar('--surface'), fillOpacity: 1 })
      .addTo(MAP.L.transit).bindTooltip(esc(st.name) + ' · Metro', { direction: 'top', offset: [0, -4] });
  });
  drawTransitLabels();
}
// Metro/letbane stations are dense (Cityring stops sit ~500 m apart), so print
// every name only once zoomed in — otherwise the regional view is unreadable.
// Hover tooltips cover the names at lower zoom.
const TRANSIT_LABEL_ZOOM = 13;
function drawTransitLabels() {
  const lay = MAP.L.transitLabels; if (!lay) return;
  lay.clearLayers();
  const tr = S.meta && S.meta.transit;
  if (!tr || !MAP.map || MAP.map.getZoom() < TRANSIT_LABEL_ZOOM) return;
  (tr.stations || []).forEach(st => {
    if (!inTrackedRegion(st.lat, st.lon)) return;
    L.marker([st.lat, st.lon], { icon: L.divIcon({ className: 'st-name', html: esc(st.name), iconSize: [90, 12], iconAnchor: [-6, 6] }), interactive: false, keyboard: false }).addTo(lay);
  });
}
function applyTransitVisibility() {
  if (!MAP.map || !MAP.L.transit) return;
  [MAP.L.transit, MAP.L.transitLabels].forEach(lay => {
    if (!lay) return;
    if (S.showMetro) { if (!MAP.map.hasLayer(lay)) lay.addTo(MAP.map); }
    else if (MAP.map.hasLayer(lay)) MAP.map.removeLayer(lay);
  });
}
function drawRail() {
  MAP.L.rail.clearLayers();
  const col = LINE_COLORS();
  const stMap = Object.fromEntries(S.meta.stations.map(s => [s.name, s]));
  const rg = S.meta.railGeom;
  if (rg && Object.keys(rg).length) {
    // Real S-tog track geometry, one colour per line ref (A/B/Bx/C/E/F/H).
    const smid = (STOG_ORDER.length - 1) / 2;
    STOG_ORDER.filter(ref => rg[ref]).forEach(ref => {
      const c = STOG_COLORS[ref] || col.central;
      const off = (STOG_ORDER.indexOf(ref) - smid) * STOG_OFFSET_M;
      (rg[ref] || []).forEach(seg => clipToRegion(offsetLatLngs(seg, off)).forEach(run =>
        L.polyline(run, { renderer: MAP.renderer, color: c,
          weight: 2.2, opacity: .85, lineCap: 'round', lineJoin: 'round' }).addTo(MAP.L.rail)));
    });
    // Kystbanen (regional): real track geometry (key "kyst") if present, else
    // fall back to the station-based dashed line.
    if (rg.kyst) {
      rg.kyst.forEach(seg => clipToRegion(seg).forEach(run =>
        L.polyline(run, { renderer: MAP.renderer, color: col.kystbanen,
          weight: 2.2, opacity: .85, lineCap: 'round', lineJoin: 'round', dashArray: '3 7' }).addTo(MAP.L.rail)));
    } else {
      (S.meta.lines || []).filter(Ln => Ln.corridor === 'kystbanen').forEach(Ln => {
        const pts = Ln.stops.map(n => stMap[n]).filter(Boolean).map(s => [s.lat, s.lon]);
        L.polyline(pts, { color: col.kystbanen, weight: 2.2, opacity: .85, lineCap: 'round', lineJoin: 'round', dashArray: '3 7' }).addTo(MAP.L.rail);
      });
    }
    return;
  }
  // Fallback (no geometry available): the old straight station-to-station lines.
  S.meta.lines.forEach(Ln => {
    const pts = Ln.stops.map(n => stMap[n]).filter(Boolean).map(s => [s.lat, s.lon]);
    L.polyline(pts, { color: col[Ln.corridor], weight: 2.2, opacity: .85, lineCap: 'round', lineJoin: 'round', dashArray: Ln.corridor === 'kystbanen' ? '3 7' : null }).addTo(MAP.L.rail);
  });
}
function drawStations() {
  MAP.L.stations.clearLayers();
  const col = LINE_COLORS();
  S.meta.stations.forEach(s => {
    if (!inTrackedRegion(s.lat, s.lon)) return;
    // corridor is either a curated corridor name or a real S-tog line ref (A, C, …)
    const sc = col[s.corridor] || STOG_COLORS[s.corridor] || col.central;
    L.circleMarker([s.lat, s.lon], { renderer: MAP.renderer, radius: s.strain ? 4 : 3, color: sc, weight: 2, fillColor: cssVar('--surface'), fillOpacity: 1 })
      .addTo(MAP.L.stations).bindTooltip(esc(s.name) + (s.strain ? '' : ' · Kystbanen'), { direction: 'top', offset: [0, -4] });
  });
  drawStationLabels();
}
// S-tog station names appear only once zoomed in — the same zoom-based behaviour
// as the metro labels, so the regional view stays uncluttered.
function drawStationLabels() {
  const lay = MAP.L.stationLabels; if (!lay) return;
  lay.clearLayers();
  if (!S.meta || !MAP.map || MAP.map.getZoom() < TRANSIT_LABEL_ZOOM) return;
  S.meta.stations.forEach(s => {
    if (!inTrackedRegion(s.lat, s.lon)) return;
    L.marker([s.lat, s.lon], { icon: L.divIcon({ className: 'st-name', html: esc(s.name), iconSize: [90, 12], iconAnchor: [-6, 6] }), interactive: false, keyboard: false }).addTo(lay);
  });
}
// Amenity overlay (issue #7): supermarkets / schools / daycare from data/poi.json.
// ~1900 points, so only drawn when the toggle is on AND zoomed in past POI_ZOOM,
// otherwise the regional view is an unreadable dot-cloud.
function drawPois() {
  const lay = MAP.L.pois; if (!lay) return;
  lay.clearLayers();
  if (!S.poi || !S.showPois || !MAP.map || MAP.map.getZoom() < POI_ZOOM) return;
  (S.poi.items || []).forEach(p => {
    const st = POI_STYLE[p.k]; if (!st) return;
    L.circleMarker([p.lat, p.lon], { renderer: MAP.renderer, radius: 3, color: st.c, weight: 1.3, fillColor: st.c, fillOpacity: .5 })
      .addTo(lay).bindTooltip((p.n ? esc(p.n) + ' · ' : '') + st.label, { direction: 'top', offset: [0, -3] });
  });
}
function drawBoundaries() {
  MAP.L.boundaries.clearLayers();
  const partial = S.munis.size > 0 && S.munis.size < S.meta.municipalities.length;
  const choro = MAP._choro;
  S.meta.municipalities.forEach(m => {
    const g = S.geo[m.slug]; if (!g) return;
    const sel = S.munis.has(m.slug);
    const choroFill = choro && choro.med[m.slug] != null;
    g.rings.forEach(ring => {
      L.polygon(ring.map(p => [p[1], p[0]]), choro ? {
        color: cssVar('--muted'), weight: sel ? 1.5 : 1, opacity: sel ? 0.7 : 0.3,
        fill: choroFill && sel, fillColor: choroFill ? choro.scale(choro.med[m.slug]) : 'transparent', fillOpacity: choroFill && sel ? 0.72 : 0,
        interactive: false, renderer: MAP.renderer,
      } : {
        color: sel ? cssVar('--land-sel-edge') : cssVar('--muted'),
        weight: sel ? 2 : 1, opacity: partial ? (sel ? 0.95 : 0.28) : 0.5,
        fill: partial && sel, fillColor: cssVar('--condo'), fillOpacity: 0.07,
        interactive: false, renderer: MAP.renderer,
      }).addTo(MAP.L.boundaries);
    });
  });
}
// precise Danish type label (Rækkehus, Fritidshus, Villalejl. …) when the
// build provides one; otherwise the coarse house/apartment split.
// function declaration (not const) so it's hoisted — it's called from render
// paths that run before this point in the file.
function homeTypeLabel(r) { return r.sub || (r.t === 'villa' ? 'Villa' : 'Ejerlejl.'); }
function listingTip(r) {
  return `<div class="tt-title">${esc(r.adr)}</div><div class="tt-row"><span>${esc(r.city)}</span><b>${homeTypeLabel(r)}</b></div><div class="tt-row"><span>Pris</span><b>${krM(r.p)}</b></div><div class="tt-row"><span>Pris/m²</span><b>${m2(r.m2p)}</b></div><div class="tt-row"><span>Størrelse</span><b>${dims(r)}</b></div><div class="tt-row"><span>Liggetid</span><b>${r.d != null ? r.d + ' dage' : '–'}</b></div>${r.ssn ? `<div class="tt-row"><span>S-tog</span><b>${esc(r.ssn)} · ${r.sst} m</b></div>` : ''}${r.row === 0 ? `<div class="tt-row"><span>🌊 Vandkant</span></div>` : ''}`;
}
// dots grow as you zoom in — keeps them visible and easy to hover/hit
const radiusForZoom = z => Math.max(4, Math.min(11, 4 + (z - 10) * 1.15));
// asking price rounded to the nearest 250.000 kr, for the on-map labels
function priceLabel(p) {
  const v = Math.round(p / 250000) * 250000;
  return v >= 1e6 ? (v / 1e6).toLocaleString('da-DK', { maximumFractionDigits: 2 }) + ' mio.'
    : Math.round(v / 1000) + 'k';
}
const LABEL_ZOOM = 13;      // show price labels from this zoom in
const LABEL_MAX = 220;      // …but never more than this many at once

function drawListings(f) {
  MAP.L.listings.clearLayers();
  MAP.f = f;
  let cAcc;
  if (S.colorBy === 'type') cAcc = r => r.t === 'villa' ? cssVar('--villa') : cssVar('--condo');
  else if (S.colorBy === 'kommune') { const ch = MAP._choro; cAcc = r => (ch && ch.med[r.muni] != null) ? ch.scale(ch.med[r.muni]) : cssVar('--muted'); }
  else { const scale = makeScale(f.map(r => r[S.colorBy]).filter(v => v != null), S.colorBy === 'd'); cAcc = r => scale(r[S.colorBy]); }
  const rad = radiusForZoom(MAP.map.getZoom());
  f.forEach(r => {
    const mk = L.circleMarker([r.lat, r.lon], { renderer: MAP.renderer, radius: rad, color: cssVar('--surface'), weight: .7, fillColor: cAcc(r), fillOpacity: .95 });
    mk.on('mouseover', ev => showTip(listingTip(r), ev.originalEvent.clientX, ev.originalEvent.clientY));
    mk.on('mousemove', ev => showTip(listingTip(r), ev.originalEvent.clientX, ev.originalEvent.clientY));
    mk.on('mouseout', hideTip);
    mk.on('click', () => { if (r.url) window.open(r.url, '_blank', 'noopener'); });
    mk.addTo(MAP.L.listings);
  });
  drawPriceLabels();
}

function resizeDots() {
  const rad = radiusForZoom(MAP.map.getZoom());
  MAP.L.listings.eachLayer(l => l.setRadius && l.setRadius(rad));
}

function drawPriceLabels() {
  const lay = MAP.L.labels; if (!lay) return;
  lay.clearLayers();
  const z = MAP.map.getZoom();
  if (z < LABEL_ZOOM || !MAP.f) return;
  const b = MAP.map.getBounds();
  const vis = MAP.f.filter(r => b.contains([r.lat, r.lon]));
  if (vis.length > LABEL_MAX) return;          // too dense to be readable
  const off = radiusForZoom(z) + 6;   // bubble tail sits just above the dot
  vis.forEach(r => {
    L.marker([r.lat, r.lon], {
      interactive: false, keyboard: false,
      icon: L.divIcon({
        className: 'price-label',
        html: `<b class="${r.t === 'villa' ? 'villa' : 'condo'}">${priceLabel(r.p)}</b>`,
        iconSize: [80, 22], iconAnchor: [40, off + 22],
      }),
    }).addTo(lay);
  });
}
function drawGeoPoints() {
  MAP.L.geo.clearLayers();
  const add = (pt, rad, emoji) => {
    if (!pt) return;
    L.circle([pt.lat, pt.lon], { radius: rad * 1000, color: cssVar('--condo'), weight: 1.5, dashArray: '5 5', fillColor: cssVar('--condo'), fillOpacity: .06 }).addTo(MAP.L.geo);
    L.marker([pt.lat, pt.lon], { icon: L.divIcon({ className: 'geo-pin2', html: `<span>${emoji}</span>`, iconSize: [30, 30], iconAnchor: [15, 15] }) }).addTo(MAP.L.geo).bindTooltip(esc(pt.name), { direction: 'top', offset: [0, -12] });
  };
  add(S.A, S.radA, '🏠'); add(S.B, S.radB, '💼');
}

// per-kommune median kr/m² choropleth (colour-by "kommune")
function choroData(f) {
  const byM = new Map();
  f.forEach(r => { if (r.m2p) (byM.get(r.muni) || byM.set(r.muni, []).get(r.muni)).push(r.m2p); });
  const med = {}; byM.forEach((arr, slug) => { const m = median(arr); if (m) med[slug] = Math.round(m); });
  return { med, scale: makeScale(Object.values(med), false) };
}
function drawMap(f) {
  if (!MAP.inited) return;
  MAP._choro = S.colorBy === 'kommune' ? choroData(f) : null;
  drawBoundaries();
  drawListings(f);
  drawGeoPoints();
  renderMapFacts(f);
  renderMapLegend(S.colorBy, f, LINE_COLORS());
}

function refreshMapTheme() { if (MAP.inited) { setTiles(); drawRail(); drawStations(); drawTransit(); } }
function fitAll() { if (MAP.map) MAP.map.fitBounds(homeBounds(), { padding: [6, 6] }); }
function fitToSelection() {
  if (!MAP.map) return;
  const pts = [S.A, S.B].filter(Boolean);
  if (pts.length) {
    let bnd = null;
    pts.forEach(p => { const bb = L.latLng(p.lat, p.lon).toBounds((p === S.A ? S.radA : S.radB) * 2000); bnd = bnd ? bnd.extend(bb) : bb; });
    MAP.map.fitBounds(bnd, { padding: [24, 24] }); return;
  }
  const sel = [...S.munis].map(s => S.geo[s]).filter(Boolean);
  if (!sel.length || sel.length === S.meta.municipalities.length) { fitAll(); return; }
  let a = 1e9, b = 1e9, c = -1e9, d = -1e9;
  sel.forEach(g => { a = Math.min(a, g.bbox[1]); b = Math.min(b, g.bbox[0]); c = Math.max(c, g.bbox[3]); d = Math.max(d, g.bbox[2]); });
  MAP.map.fitBounds([[a, b], [c, d]], { padding: [18, 18] });
}
// Zoom the map to the bounding box of a set of listings — used when the search
// narrows to a postnummer so the map shows just that area (e.g. 2900 Hellerup).
function fitToPoints(f) {
  if (!MAP.map) return false;
  const pts = f.filter(r => r.lat && r.lon);
  if (!pts.length) return false;
  if (pts.length === 1) { MAP.map.setView([pts[0].lat, pts[0].lon], 15); return true; }
  let a = 1e9, b = 1e9, c = -1e9, d = -1e9;
  pts.forEach(r => { a = Math.min(a, r.lat); c = Math.max(c, r.lat); b = Math.min(b, r.lon); d = Math.max(d, r.lon); });
  MAP.map.fitBounds([[a, b], [c, d]], { padding: [40, 40], maxZoom: 15 });
  return true;
}

function renderMapFacts(f) {
  const box = $('#mapFacts'); box.innerHTML = '';
  if (!f.length) return;
  const names = Object.fromEntries(S.meta.municipalities.map(m => [m.slug, m.name]));
  const byM = new Map(); f.forEach(r => { (byM.get(r.muni) || byM.set(r.muni, []).get(r.muni)).push(r.m2p); });
  const meds = [...byM.entries()].map(([s, a]) => ({ n: names[s] || s, v: median(a.filter(Boolean)) })).filter(x => x.v).sort((a, b) => b.v - a.v);
  const nearMed = median(f.filter(r => r.near).map(r => r.m2p).filter(Boolean));
  const farMed = median(f.filter(r => !r.near).map(r => r.m2p).filter(Boolean));
  // Metro only exists in a handful of central kommuner, so comparing "near
  // metro" against the *whole* filtered set (unlike S-tog, which reaches
  // ~20 kommuner and so already spans a comparable mix on both sides) would
  // pit central Copenhagen against far, cheap kommuner that have nothing to
  // do with metro access — inflating the premium with general urban-core
  // desirability, not a metro-specific effect. Restrict "far" to the same
  // kommuner that actually have metro-adjacent listings, so it's "near a
  // stop" vs. "same city, just >500 m from one".
  const mNearRows = f.filter(r => nearMetro(r));
  const mNearMed = median(mNearRows.map(r => r.m2p).filter(Boolean));
  const metroKommuner = new Set(mNearRows.map(r => r.muni));
  const mFarArr = f.filter(r => r.mst != null && !nearMetro(r) && metroKommuner.has(r.muni)).map(r => r.m2p).filter(Boolean);
  const mNearN = mNearRows.length;
  const mFarMed = median(mFarArr);
  const facts = [];
  if (meds.length) facts.push(['Dyreste kommune', `${meds[0].n} · ${m2(meds[0].v)}`]);
  if (meds.length > 1) facts.push(['Billigste kommune', `${meds[meds.length - 1].n} · ${m2(meds[meds.length - 1].v)}`]);
  if (nearMed && farMed) { const prem = Math.round((nearMed / farMed - 1) * 100); facts.push(['Nær S-tog vs. længere væk', `${m2(nearMed)} <small>mod ${m2(farMed)}</small>`]); facts.push(['S-togs­præmie', (prem >= 0 ? '+' : '') + prem + ' <small>% pr. m²</small>']); }
  if (mNearMed && mFarMed && mNearN >= 8 && mFarArr.length >= 8) {
    const mp = Math.round((mNearMed / mFarMed - 1) * 100);
    facts.push(['Metropræmie', (mp >= 0 ? '+' : '') + mp + ' <small>% pr. m²</small>',
      'Sammenlignet med boliger i samme kommuner (metro findes kun i få, i forvejen dyre kommuner) — ellers ville præmien blande metronærhed sammen med prisforskellen mellem centrum og resten af regionen.']);
  }
  facts.forEach(([l, v, title]) => box.append(el('div', { class: 'mf', ...(title ? { title } : {}) }, el('div', { class: 'mf-l' }, l), el('div', { class: 'mf-v', html: v }))));
}

function renderMapLegend(colorBy, f, lineColors) {
  const box = $('#mapLegend'); box.innerHTML = '';
  if (colorBy === 'type') box.append(legItem(cssVar('--condo'), 'Ejerlejlighed'), legItem(cssVar('--villa'), 'Villa'));
  else {
    const vals = colorBy === 'kommune'
      ? (MAP._choro ? Object.values(MAP._choro.med) : [])
      : f.map(r => r[colorBy]).filter(v => v != null);
    const lo = quantile(vals, .05), hi = quantile(vals, .95);
    const fmt = colorBy === 'p' ? krM : colorBy === 'd' ? v => Math.round(v) + ' dage' : m2;
    const ramp = el('span', { class: 'ramp' }); let steps = seqRamp(); if (colorBy === 'd') steps = [...steps].reverse();
    steps.forEach(c => ramp.append(el('i', { style: `background:${c}` })));
    const label = colorBy === 'm2p' ? 'Pris pr. m²' : colorBy === 'p' ? 'Pris' : colorBy === 'kommune' ? 'Median pris/m² pr. kommune' : 'Liggetid';
    // mobile: compact "23k…104k" (the label already states the unit) instead
    // of "23.254 kr/m²…104.363 kr/m²", which alone ran ~140px too wide to
    // share a line with the ramp swatch on a 390px phone.
    const scaleFmt = MOBILE_MQ.matches && colorBy !== 'd' ? kc : fmt;
    // one flex item, not four — otherwise the wrapping legend row (mobile) can
    // split the scale's low/ramp/high apart onto separate lines, since each
    // used to be its own independently-wrappable child.
    box.append(el('span', { class: 'legend-item' }, label + ': ' + (colorBy === 'd' ? 'kort' : scaleFmt(lo)), ramp, colorBy === 'd' ? 'lang' : scaleFmt(hi)));
  }
  if (S.showRail) {
    const rg = S.meta.railGeom;
    if (rg && Object.keys(rg).length) {
      STOG_ORDER.filter(r => rg[r]).forEach(r => box.append(el('span', { class: 'legend-item' }, el('span', { class: 'legend-line', style: `border-top-color:${STOG_COLORS[r]}` }), 'S-tog ' + r)));
      if ((S.meta.lines || []).some(L => L.corridor === 'kystbanen'))
        box.append(el('span', { class: 'legend-item' }, el('span', { class: 'legend-line dashed', style: `border-top-color:${lineColors.kystbanen}` }), 'Kystbanen'));
    } else {
      S.meta.lines.forEach(L => box.append(el('span', { class: 'legend-item' }, el('span', { class: 'legend-line' + (L.corridor === 'kystbanen' ? ' dashed' : ''), style: `border-top-color:${lineColors[L.corridor]}` }), L.label)));
    }
  }
  if (S.showMetro && S.meta.transit) {
    const lines = S.meta.transit.lines || [];
    const lineRef = l => {
      if (l.ref && METRO_REFS[l.ref]) return l.ref;
      const tc = l.colour && /^#?[0-9a-fA-F]{6}$/.test(l.colour) ? (l.colour[0] === '#' ? l.colour : '#' + l.colour).toLowerCase() : null;
      return tc && METRO_COL_REF[tc] || null;
    };
    const refs = METRO_REF_ORDER.filter(r => lines.some(l => l.mode !== 'letbane' && lineRef(l) === r));
    refs.forEach(r => box.append(el('span', { class: 'legend-item' }, el('span', { class: 'legend-line', style: `border-top-color:${METRO_REFS[r]}` }), r)));
    if (lines.some(l => l.mode === 'metro' && !METRO_REF_ORDER.includes(l.ref)) && !refs.length)
      box.append(el('span', { class: 'legend-item' }, el('span', { class: 'legend-line', style: `border-top-color:${METRO_COLORS.metro}` }), 'Metro'));
    if (lines.some(l => l.mode === 'letbane')) box.append(el('span', { class: 'legend-item' }, el('span', { class: 'legend-line dashed', style: `border-top-color:${METRO_COLORS.letbane}` }), 'Letbane (Ring 3)'));
    // All local/regional lines share one colour+dash style (unlike S-tog,
    // they aren't individually distinguishable on the map), so a row per
    // line just repeats the same swatch — often 7+ times — and made the
    // legend column far taller than the map itself. One row communicates
    // the same thing.
    const lokalLines = lines.filter(l => l.mode === 'lokal');
    if (lokalLines.length) box.append(el('span', { class: 'legend-item' }, el('span', { class: 'legend-line dashed', style: `border-top-color:${LOKAL_COLOR}` }),
      lokalLines.length > 1 ? 'Lokal-/regionaltog' : (lokalLines[0].name || lokalLines[0].ref)));
  }
  if (S.showPois && S.poi) box.append(legItem(POI_STYLE.supermarket.c, 'Indkøb'), legItem(POI_STYLE.school.c, 'Skole'), legItem(POI_STYLE.kindergarten.c, 'Daginstitution'));
}
function legItem(color, text) { return el('span', { class: 'legend-item' }, el('span', { class: 'swatch', style: `background:${color}` }), text); }

/* ===================== listing cards ===================== */
// robust z-score of each home's kr/m² within its (kommune, type) group — memoized
let _zMap = null;
function zMap() {
  if (_zMap) return _zMap;
  _zMap = new Map();
  const groups = new Map();
  S.all.forEach(r => { if (!r.m2p) return; const k = r.muni + '|' + r.t; (groups.get(k) || groups.set(k, []).get(k)).push(r); });
  groups.forEach(rows => {
    if (rows.length < 8) return;
    const vals = rows.map(r => r.m2p), med = median(vals), mad = median(vals.map(v => Math.abs(v - med))), sig = 1.4826 * mad;
    if (!sig) return;
    rows.forEach(r => _zMap.set(r.id, (r.m2p - med) / sig));
  });
  return _zMap;
}
// "possible bargain" score: cheap per m² for its area, bonus for a recent price
// cut and for being near an S-train.
// Rank for the "under vurdering" sort. Homes the model itself treated as
// atypical (excluded from the fit) are pushed to the bottom — a houseboat at a
// fraction of the local kr/m² is not a bargain, it's a different product.
function valueRank(r) {
  const FV = fairValue();
  if (FV.odd.has(r.id)) return 1e9;
  return FV.resid.get(r.id) ?? 1e9;
}
function fundScore(r) {
  const z = zMap().get(r.id);
  if (z == null) return -Infinity;
  if (fairValue().odd.has(r.id)) return -Infinity;   // atypical — not a "find"
  return -z + (r.chg < 0 ? 0.6 : 0) + (r.near ? 0.3 : 0);
}

/* The hedonic fair-value model lives in model.js so index.html and the
   dedicated model page share one implementation. */
const fairValue = () => BT.fairValue(S.all, S.sold);
function sortRows(f) {
  const cmp = { d: (a, b) => a.d - b.d, m2p: (a, b) => a.m2p - b.m2p, m2p_desc: (a, b) => b.m2p - a.m2p, p: (a, b) => a.p - b.p, p_desc: (a, b) => b.p - a.p, sst: (a, b) => a.sst - b.sst, chg: (a, b) => (a.chg || 0) - (b.chg || 0), fund: (a, b) => fundScore(b) - fundScore(a), value: (a, b) => (valueRank(a)) - (valueRank(b)) }[S.sort];
  return [...f].sort(cmp);
}
// Per-listing change log (data/tracker.json) — loaded lazily after first paint
// so it never blocks the initial render; cards refresh once it arrives.
function loadTracker() {
  fetch('data/tracker.json').then(r => r.json()).then(t => {
    S.trackerMap = new Map(Object.entries(t.items || {}));
    renderCards(filtered());
    renderPriceChanges(filtered());   // 'sold/removed' view depends on the tracker
  }).catch(() => {});
}
const MONTHS_DA = ['jan', 'feb', 'mar', 'apr', 'maj', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec'];
function fmtDay(iso) { if (!iso) return ''; const [, m, d] = iso.split('-'); return `${+d}. ${MONTHS_DA[+m - 1]}`; }

/* ---- saved homes (favourites) — device-local via localStorage ---- */
function loadFavs() { try { return JSON.parse(localStorage.getItem('btFavs') || '{}'); } catch (e) { return {}; } }
function saveFavs() { try { localStorage.setItem('btFavs', JSON.stringify(S.favs)); } catch (e) { /* ignore */ } }
function isFav(id) { return !!S.favs[String(id)]; }
function toggleFav(id, price) {
  id = String(id);
  if (S.favs[id]) delete S.favs[id]; else S.favs[id] = { p: price || null, at: Date.now() };
  saveFavs();
  const n = Object.keys(S.favs).length;
  const lbl = $('#onlyFavLabel'); if (lbl) lbl.textContent = n ? `Kun gemte (${n})` : 'Kun gemte';
}
// tiny inline sparkline of a listing's observed asking prices
function priceSpark(events) {
  const ps = events.map(e => e[1]).filter(v => v != null);
  if (ps.length < 2) return null;
  const w = 56, h = 16, min = Math.min(...ps), max = Math.max(...ps), rng = (max - min) || 1, step = w / (ps.length - 1);
  const pts = ps.map((v, i) => `${(i * step).toFixed(1)},${(h - 2 - (v - min) / rng * (h - 4)).toFixed(1)}`).join(' ');
  const svg = svel('svg', { viewBox: `0 0 ${w} ${h}`, class: 'spark', width: w, height: h, 'aria-hidden': 'true' });
  svg.append(svel('polyline', { points: pts, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
  return svg;
}

function renderCards(f) {
  const rows = sortRows(f);
  $('#listCount').textContent = '· ' + f.length.toLocaleString('da-DK');
  const box = $('#cards'); box.innerHTML = ''; box.removeAttribute('aria-busy');
  rows.slice(0, S.shown).forEach(r => box.append(card(r)));
  const more = $('#loadMore'); more.hidden = rows.length <= S.shown; more.textContent = `Vis flere (${(rows.length - S.shown).toLocaleString('da-DK')} tilbage)`;
}
function card(r) {
  const a = el('a', { class: 'lcard', href: r.url || '#', target: '_blank', rel: 'noopener' });
  // no listing photo (avoids republishing broker/boligsiden images) — a type-tinted
  // placeholder holds the badges instead
  const thumb = el('div', { class: 'thumb ' + r.t });
  const fav = el('button', { class: 'fav' + (isFav(r.id) ? ' on' : ''), type: 'button', title: isFav(r.id) ? 'Fjern fra gemte' : 'Gem bolig', 'aria-label': isFav(r.id) ? 'Fjern fra gemte' : 'Gem bolig', 'aria-pressed': isFav(r.id) ? 'true' : 'false' }, isFav(r.id) ? '♥' : '♡');
  fav.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); toggleFav(r.id, r.p); renderCards(filtered()); });
  thumb.append(fav);
  thumb.append(el('span', { class: 'badge ' + r.t }, homeTypeLabel(r)));
  if (r.d != null && r.d <= NEW_DAYS) thumb.append(el('span', { class: 'newbadge' }, 'Ny'));
  if (r.ssn) thumb.append(el('span', { class: 'stbadge' + (r.near ? ' near' : '') }, `${r.near ? '🚆 ' : ''}${r.ssn} · ${(r.sst / 1000).toLocaleString('da-DK', { maximumFractionDigits: 1 })} km`));
  a.append(thumb);
  const body = el('div', { class: 'body' });
  body.append(el('div', { class: 'price' }, krM(r.p)));
  body.append(el('div', { class: 'addr' }, r.adr));
  body.append(el('div', { class: 'city' }, (r.zip ? r.zip + ' ' : '') + r.city));
  const meta = el('div', { class: 'meta' });
  meta.append(el('span', {}, el('b', {}, m2(r.m2p))));
  if (r.a) meta.append(el('span', {}, `${r.a} m²`));
  if (r.t === 'villa' && r.lot) meta.append(el('span', {}, `grund ${r.lot.toLocaleString('da-DK')} m²`));
  if (r.r) meta.append(el('span', {}, `${r.r} vær.`));
  if (r.y) meta.append(el('span', {}, `opf. ${r.y}`));
  // "E: E" read as a typo rather than an energimærke of E — spell the label out
  if (r.e) meta.append(el('span', { title: 'Energimærke' },
    `Energi ${String(r.e).toUpperCase().replace('2015', ' 2015')}`));
  if (r.d != null) meta.append(el('span', {}, `${r.d} dage`));
  if (r.chg < 0) meta.append(el('span', { class: 'cut' }, `↓ ${Math.abs(r.chg).toLocaleString('da-DK', { maximumFractionDigits: 1 })} %`));
  if (r.poi) {
    const fmtM = m => m < 950 ? m + ' m' : (m / 1000).toLocaleString('da-DK', { maximumFractionDigits: 1 }) + ' km';
    const pp = [];
    if (r.poi.sup != null) pp.push('🛒 ' + fmtM(r.poi.sup));
    if (r.poi.sch != null) pp.push('🏫 ' + fmtM(r.poi.sch));
    if (r.poi.day != null) pp.push('🧸 ' + fmtM(r.poi.day));
    if (pp.length) meta.append(el('span', { class: 'poi-dist', title: 'Afstand til nærmeste indkøb · skole · daginstitution' }, pp.join(' · ')));
  }
  // Shown from 55 dB up. r.db is the midpoint of a 5 dB band, so the lowest one
  // shown (53–58) sits *around* the 58 dB vejledende grænseværdi rather than
  // above it — that gets a muted style and its own wording, while 60 and up,
  // which clears the limit outright, keeps the warning colour. Same number,
  // honestly qualified, instead of one badge implying two different things.
  if (r.db >= 55) {
    const over = r.db >= 60;
    meta.append(el('span', {
      class: over ? 'noise-db' : 'noise-db near',
      title: over
        ? 'Beregnet vejstøj på facaden (Lden), midt i et 5 dB-interval fra Miljøstyrelsens '
          + 'støjkortlægning. Over den vejledende grænseværdi for boliger på 58 dB.'
        : 'Beregnet vejstøj på facaden (Lden) fra Miljøstyrelsens støjkortlægning. Ligger i '
          + 'intervallet 53–58 dB, altså omkring den vejledende grænseværdi for boliger på 58 dB.',
    }, `🔊 ~${r.db} dB`));
  }
  if (S.A || S.B) {
    const parts = [];
    if (S.A) parts.push('🏠 ' + haversine(r.lat, r.lon, S.A.lat, S.A.lon).toLocaleString('da-DK', { maximumFractionDigits: 1 }) + ' km');
    if (S.B) parts.push('💼 ' + haversine(r.lat, r.lon, S.B.lat, S.B.lon).toLocaleString('da-DK', { maximumFractionDigits: 1 }) + ' km');
    meta.append(el('span', { class: 'commute-dist' }, parts.join(' · ')));
  }
  body.append(meta);
  // row 0 = this parcel's own boundary meets the coastline (cadastral fact,
  // not a distance guess) — see annotate_parcel_row in build_data.py
  if (r.row === 0) {
    body.append(el('div', { class: 'waterbadge', title: 'Grunden grænser op til kysten, ifølge matrikelkortet.' }, '🌊 Vandkant'));
  }
  if (r.hf) {
    // kommune land-reversion clause — flag it; its low price isn't a "deal"
    body.append(el('div', { class: 'hfbadge', title: 'Kommunen har hjemfaldspligt / tilbagekøbsret på grunden — kan sænke værdien markant. Læs annoncen.' }, '⚠ Hjemfald / tilbagekøb'));
  } else {
    // fair-value model: how the asking kr/m² compares to what the model predicts
    const FV = fairValue();
    const fvr = FV.resid.get(r.id), fvp = FV.pred.get(r.id);
    // only flag a deviation that clearly exceeds the model's own uncertainty —
    // otherwise we'd label ordinary noise a bargain
    const band = FV.mape != null ? Math.max(6, FV.mape) : 6;
    // homes the model itself treats as atypical (houseboat, andelslignende,
    // encumbered plot …) get no verdict — a bogus "under vurdering" on those
    // would read as a bargain when it's really a different kind of property
    if (fvr != null && !FV.odd.has(r.id) && Math.abs(fvr) >= band) {
      const under = fvr < 0, mag = Math.abs(fvr);
      // cap the shown magnitude so a genuine oddball (andel, encumbered plot…)
      // doesn't scream "50 %+" — the exact figure stays in the tooltip
      const shown = mag > 50 ? '50+' : mag;
      const range = (FV.lo && FV.hi) ? ` · forventet spænd ${m2(Math.round(fvp * FV.lo))}–${m2(Math.round(fvp * FV.hi))}` : '';
      const acc = FV.mape != null ? ` · modellens typiske afvigelse ±${FV.mape} %` : '';
      body.append(el('div', { class: 'valbadge ' + (under ? 'down' : 'up'), title: `Model-vurdering: ${m2(fvp)}${range} · udbudt ${m2(r.m2p)} (${under ? '−' : '+'}${mag} %)${acc}` },
        `${under ? '↓' : '↑'} ${shown} % ${under ? 'under' : 'over'} vurdering`));
    }
    // local realised benchmark — nearby tinglyste salg (same type, last 12 mo)
    if (r.cmpM2 && r.m2p) {
      const diff = Math.round((r.m2p / r.cmpM2 - 1) * 100);
      const cmpTitle = `${r.cmpN} tinglyste salg inden for ${r.cmpR} m, justeret til nutidsniveau`
        + (r.cmpS != null ? ` · spredning ±${Math.round(r.cmpS / 2)} %` : '')
        + (r.a ? ' · matchet på størrelse og byggeår' : '');
      body.append(el('div', { class: 'compline', title: cmpTitle },
        `Realiseret i området: ${m2(r.cmpM2)} · udbudt ${Math.abs(diff)} % ${diff >= 0 ? 'over' : 'under'}`));
    }
  }
  // if you saved this home and its asking price has changed since, flag it
  const fv = S.favs[String(r.id)];
  if (fv && fv.p && r.p && fv.p !== r.p) {
    const delta = r.p - fv.p;
    body.append(el('div', { class: 'fav-change ' + (delta < 0 ? 'down' : 'up') },
      `${delta < 0 ? '↘' : '↗'} ${(delta < 0 ? '−' : '+') + Math.abs(delta).toLocaleString('da-DK')} kr siden du gemte den`));
  }
  // observed price trajectory since we started following this listing
  const tk = S.trackerMap && S.trackerMap.get(String(r.id));
  if (tk && tk.events && tk.events.length >= 2) {
    const first = tk.events[0][1], last = tk.events[tk.events.length - 1][1];
    const delta = (last || 0) - (first || 0);
    const ph = el('div', { class: 'phist ' + (delta < 0 ? 'down' : delta > 0 ? 'up' : 'flat') });
    const spark = priceSpark(tk.events); if (spark) ph.append(spark);
    const arrow = delta < 0 ? '↘' : delta > 0 ? '↗' : '→';
    const txt = delta === 0 ? 'uændret' : (delta < 0 ? '−' : '+') + Math.abs(delta).toLocaleString('da-DK') + ' kr';
    ph.append(el('span', { class: 'ph-txt' }, `${arrow} ${txt} siden ${fmtDay(tk.firstSeen)}`));
    body.append(ph);
  }
  a.append(body);
  return a;
}

boot();
