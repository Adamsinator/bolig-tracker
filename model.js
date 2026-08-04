/* Bolig Tracker — hedonic fair-value model, shared by the listings page and
   the model page (model.html). Plain script, wrapped in an IIFE so its
   helpers don't collide with app.js's globals; exports onto window.BT. */
(function () {
  'use strict';
  const median = arr => { if (!arr.length) return null; const a = [...arr].sort((x, y) => x - y); const m = a.length >> 1; return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; };
  const METRO_NEAR_M = 500;   // metro only serves the dense central kommuner
  const nearMetro = r => r.mst != null && r.mst <= METRO_NEAR_M;

  /* ===== hedonic "fair value" model — kr/m² predicted from a home's features
     (size, rooms, year, floor, basement, lot, energy, distance-to-S-tog,
     distance-to-metro, local realised comps, type, kommune) via ridge regression;
     residual = asking vs. model. Built once. ===== */
  let _fv = null, _fvKey = null;
  function fairValue(listings, sold) {
    if (_fv && _fvKey === listings) return _fv;
    _fvKey = listings;
    // exclude homes with a kommune land-reversion clause — their asking price
    // reflects the encumbrance, not the home, and would distort the model
    const rows = listings.filter(r => r.m2p > 0 && r.a > 0 && !r.hf);
    const out = { pred: new Map(), resid: new Map(), r2: null, mape: null, lo: null, hi: null, n: rows.length, odd: new Set() };
    if (rows.length < 200) { _fv = out; return out; }
    const munis = [...new Set(rows.map(r => r.muni))].sort();
    const ER = { a: 7, b: 6, c: 5, d: 4, e: 3, f: 2, g: 1 };
    const erank = e => e ? (ER[String(e)[0].toLowerCase()] || 4) : 4;
    // only credit metro access when this build actually has transit data — otherwise
    // a missing mst would be imputed as distance 0 (i.e. "at a metro"), the opposite
    // of the truth. annotate_metro fills mst for all rows or none, so this is all-or-nothing.
    const hasMetro = rows.some(r => r.mst != null);
    // local realised benchmark (median kr/m² of nearby tinglyste sales, same type) —
    // a strong micro-location signal. Impute a missing comp with the kommune-type
    // realised median, then the overall comp median, so the feature is complete.
    const hasComp = rows.some(r => r.cmpM2);
    const overallComp = hasComp ? median(rows.map(r => r.cmpM2).filter(Boolean)) : null;
    const compOf = r => r.cmpM2 || (sold && sold.byMuni[r.muni] && sold.byMuni[r.muni][r.t] && sold.byMuni[r.muni][r.t].medM2) || overallComp;
    // walkability (issue #7): distance to the nearest shop / school / daycare.
    // Median-imputed so a listing without the data doesn't read as "at the door".
    const hasPoi = rows.some(r => r.poi && r.poi.sup != null);
    const medPoi = {};
    if (hasPoi) ['sup', 'sch', 'day'].forEach(k => { medPoi[k] = median(rows.map(r => r.poi && r.poi[k]).filter(v => v != null)) || 800; });
    const poiOf = (r, k) => (r.poi && r.poi[k] != null) ? r.poi[k] : medPoi[k];
    // location quality (issue #8): motorway noise vs. water and nature nearby.
    // Same median imputation so a gap never reads as "right next to it".
    // The coast is fetched separately from lakes ('sea'/'lak'); older data has
    // them pooled as 'wat'. Take whichever this build has so the model keeps
    // working across the change.
    const hasSea = rows.some(r => r.geo && r.geo.sea != null);
    const GEO_KEYS = hasSea ? ['mot', 'sea', 'lak', 'grn'] : ['mot', 'wat', 'grn'];
    const SEA = hasSea ? 'sea' : 'wat';
    const hasGeo = rows.some(r => r.geo && r.geo.mot != null);
    const medGeo = {};
    if (hasGeo) GEO_KEYS.forEach(k => { medGeo[k] = median(rows.map(r => r.geo && r.geo[k]).filter(v => v != null)) || 1000; });
    const geoOf = (r, k) => (r.geo && r.geo[k] != null) ? r.geo[k] : medGeo[k];
    // Distance to the shore alone can't say what a buyer is paying for. The
    // premium is a cliff at the water's edge, not a gradient — first row on
    // Strandvejen against second row — so add a term that decays over ~150 m on
    // top of the broad log distance, and let houses carry their own slope: a
    // garden on the water is worth more than a flat 80 m back from it.
    const seaNear = r => Math.exp(-geoOf(r, SEA) / 150);
    // road-traffic noise at the façade (Lden, dB) from Miljøstyrelsen's 2022 EU
    // noise mapping. Centred on 55 dB — the level at which the map starts — and
    // scaled by 10 so a whole band moves the feature by half a unit. A home with
    // no reading gets the median rather than "silent".
    const hasNoise = rows.some(r => r.db != null);
    const medDb = hasNoise ? (median(rows.map(r => r.db).filter(v => v != null)) || 55) : 55;
    const dbOf = r => (r.db != null ? r.db : medDb);
    const feat = r => {
      const la = Math.log(r.a);
      const f = [1, la, la * la, (r.r || 0), (r.a && r.r ? r.a / r.r / 40 : 0),
        (((r.y || 1970) - 1970) / 50), ((r.fln != null ? r.fln : 0) / 5),
        (r.bsm > 0 ? 1 : 0), (Math.log((r.lot || 0) + 1) / 10), (erank(r.e) / 7), (Math.log((r.sst || 0) + 1) / 10),
        (r.near ? 1 : 0), (r.t === 'villa' ? 1 : 0)];
      if (hasMetro) f.push(Math.log((r.mst || 0) + 1) / 10, nearMetro(r) ? 1 : 0);
      if (hasComp) f.push(Math.log(compOf(r)) / 12);
      if (hasPoi) f.push(Math.log(poiOf(r, 'sup') + 1) / 10, Math.log(poiOf(r, 'sch') + 1) / 10, Math.log(poiOf(r, 'day') + 1) / 10);
      if (hasGeo) {
        GEO_KEYS.forEach(k => f.push(Math.log(geoOf(r, k) + 1) / 10));
        f.push(seaNear(r), seaNear(r) * (r.t === 'villa' ? 1 : 0));
      }
      if (hasNoise) f.push((dbOf(r) - 55) / 10);
      for (let i = 1; i < munis.length; i++) f.push(r.muni === munis[i] ? 1 : 0);
      return f;
    };
    const p = feat(rows[0]).length, n = rows.length;
    const y = rows.map(r => Math.log(r.m2p));
    const X = rows.map(feat);   // cache the design matrix — reused by every CV fold
    // ridge-regularised normal equations, fitted on the given row indices
    const fit = idx => {
      const XtX = Array.from({ length: p }, () => new Float64Array(p)), Xty = new Float64Array(p);
      for (const i of idx) {
        const xi = X[i], yi = y[i];
        for (let a = 0; a < p; a++) { const xa = xi[a]; if (!xa) continue; Xty[a] += xa * yi; const row = XtX[a]; for (let b = 0; b < p; b++) row[b] += xa * xi[b]; }
      }
      for (let a = 1; a < p; a++) XtX[a][a] += 1;   // ridge (leave intercept)
      const A = XtX.map((row, i) => { const rr = Array.from(row); rr.push(Xty[i]); return rr; });
      for (let c = 0; c < p; c++) {
        let piv = c; for (let r = c + 1; r < p; r++) if (Math.abs(A[r][c]) > Math.abs(A[piv][c])) piv = r;
        [A[c], A[piv]] = [A[piv], A[c]];
        const pv = A[c][c] || 1e-9; for (let j = c; j <= p; j++) A[c][j] /= pv;
        for (let r = 0; r < p; r++) if (r !== c && A[r][c]) { const f = A[r][c]; for (let j = c; j <= p; j++) A[r][j] -= f * A[c][j]; }
      }
      return A.map(r => r[p]);
    };
    const dot = (beta, xi) => { let s = 0; for (let k = 0; k < p; k++) s += beta[k] * xi[k]; return s; };

    // 5-fold cross-validation: the honest accuracy is out-of-sample, so a home's
    // own asking price never helps predict itself. Gives the reported error and
    // the uncertainty band (in-sample R² alone would flatter the model).
    const K = 5, cvErr = [];
    const foldOf = i => i % K;
    for (let k = 0; k < K; k++) {
      const tr = [], te = [];
      for (let i = 0; i < n; i++) (foldOf(i) === k ? te : tr).push(i);
      if (tr.length < p + 10) continue;
      const b = fit(tr);
      for (const i of te) cvErr.push(y[i] - dot(b, X[i]));   // log-space residual
    }
    if (cvErr.length > 50) {
      const abs = cvErr.map(e => Math.abs(Math.exp(e) - 1)).sort((a, b) => a - b);
      out.mape = Math.round(abs[Math.floor(abs.length / 2)] * 100);        // median abs % error
      const q = cvErr.slice().sort((a, b) => a - b);
      out.lo = Math.exp(q[Math.floor(q.length * 0.1)]);                    // 10th–90th pct band
      out.hi = Math.exp(q[Math.floor(q.length * 0.9)]);
    }

    // Robust refit: a handful of listings aren't ordinary homes — houseboats
    // (no plot, a fraction of the local kr/m²), andelslignende sales, encumbered
    // plots. Left in, they drag the whole surface down. Fit once, then drop rows
    // more than 3 robust SDs off and refit, so the model describes normal homes.
    const b0 = fit(rows.map((_, i) => i));
    const res0 = rows.map((_, i) => y[i] - dot(b0, X[i]));
    const med = arr => { const s = arr.slice().sort((a, b) => a - b); return s[Math.floor(s.length / 2)]; };
    const m0 = med(res0), mad = med(res0.map(e => Math.abs(e - m0))) || 1e-6;
    const cut = 3 * 1.4826 * mad;
    const keep = [];
    rows.forEach((r, i) => { if (Math.abs(res0[i] - m0) <= cut) keep.push(i); else out.odd.add(r.id); });
    const beta = keep.length > p + 50 ? fit(keep) : b0;

    const ybar = y.reduce((a, b) => a + b, 0) / n; let ssr = 0, sst = 0;
    rows.forEach((r, i) => {
      const yh = dot(beta, X[i]);
      out.pred.set(r.id, Math.round(Math.exp(yh)));
      out.resid.set(r.id, Math.round((r.m2p / Math.exp(yh) - 1) * 100));
      ssr += (y[i] - yh) ** 2; sst += (y[i] - ybar) ** 2;
    });
    out.r2 = sst ? 1 - ssr / sst : null;
    _fv = out; return out;
  }

  window.BT = window.BT || {};
  BT.fairValue = fairValue;
  BT.median = median;
})();
