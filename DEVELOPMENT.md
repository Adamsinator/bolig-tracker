# Development notes

Everything needed to pick this project up on another machine. The repo is
self-contained: no build step, no package manager, no secrets.

```bash
git clone https://github.com/Adamsinator/bolig-tracker.git
cd bolig-tracker
python3 -m http.server 8777        # then open http://localhost:8777
```

> ⚠️ Opening `index.html` directly (`file://`) does **not** work — the page
> `fetch()`es `data/*.json` and browsers block that on the file protocol. Serve
> it over http.

To refresh the data locally:

```bash
pip install openpyxl        # only needed for the BVC spreadsheet
python3 scripts/build_data.py
```

## Layout

| Path | What |
|---|---|
| `index.html` / `styles.css` / `app.js` | the whole front-end, no framework |
| `model.js` | hedonic fair-value model — shared by `index.html` and `model.html`, exports `window.BT` |
| `model.html` / `modelpage.js` | model accuracy dashboard + the address-lookup valuation page |
| `vendor/leaflet/` | Leaflet 1.9.4, vendored on purpose (no JS CDN) |
| `scripts/build_data.py` | the entire data pipeline |
| `scripts/stations.py` | rail station coords + line ordering |
| `.github/workflows/build.yml` | daily cron that runs the pipeline and commits `data/` |
| `data/*.json` | generated — committed so the site works with zero backend |

## Model

`model.js`'s `fairValue()` is a ridge-regularised hedonic regression on
log(kr/m²) — the exact feature list, the ridge/CV/robust-refit math, and why
R² and the reported "typisk afvigelse" are different numbers, is documented
in full at the bottom of `model.html` ("Modellen i detaljer"), not
duplicated here to avoid the two drifting apart. `fairValue(listings, sold)`
also exposes `.predict(spec)` — call it with a hand-built `{t, a, r, y, ...}`
object (no `m2p` needed) to price something that isn't a listing at all;
that's what the address-lookup page uses.

## Data sources

| File | Source | Notes |
|---|---|---|
| `listings.json` | `api.boligsiden.dk/search/cases` | public, no key, CORS-open |
| `geo.json` | Dataforsyningen `/kommuner/{kode}?format=geojson` | kommune boundaries |
| `priceindex.json` | Danmarks Statistik table **EJ56** (JSONSTAT) | quarterly index from 1992 |
| `bvc.json` | Boligøkonomisk Videncenter `.xlsx` | real (inflation-adj.) index from 1938 |
| `history.json` | our own daily snapshots | aggregate per (kommune,type): medians, quartiles, S-tog premium; ~10y; not back-fillable |
| `tracker.json` | our own per-listing change log | each home's price trajectory + first/last seen + sold/withdrawn date; removed kept ~1y then pruned |

Address autocomplete (home/work feature) calls Dataforsyningen DAWA directly
from the browser — it sends `access-control-allow-origin: *`.

## Gotchas worth knowing

**Cache-busting.** `index.html` loads `styles.css?v=N` and `app.js?v=N`.
**Bump `N` whenever you change either file** — otherwise Pages + browser caching
happily serves the previous build and it looks like your deploy failed.

**boligsiden payload.** Each case embeds the realtor's full ratings — roughly
30 KB per listing. Fetching ~3,700 listings raw would be ~65 MB, which is why
the pipeline trims each record to ~20 fields up front. Don't try to fetch this
live from the browser. Also: `municipalities` must be **repeated** params
(`&municipalities=a&municipalities=b`); comma-separated silently returns 0 hits.
Only `perAreaPrice` and `timeOnMarket` are valid `sortBy` values.

**Station coordinates** come from OpenStreetMap via Overpass, matched by name.
The original hand-curated ones were off by a median of 582 m (max 2.7 km), which
is glaring on a tile basemap. `overpass-api.de` frequently returns 504/406 —
`overpass.kumi.systems` is the reliable mirror. Note the Frederikssund line
ordering: **Malmparken sits east of Ballerup**, so listing them the "obvious"
way makes the polyline zig-zag. Changing stations changes every listing's
`sst`/`near` values, so always re-run the build afterwards.

**Boundary simplification.** `_rdp_ring()` exists because plain Ramer–Douglas–
Peucker collapses a *closed* ring to 2 points: the first and last vertex are the
same, so the baseline has zero length and every perpendicular distance is ~0.
The fix is to split the ring at the vertex farthest from the start and simplify
each half.

**Price history is not directly available.** boligsiden exposes no public price
history or realised-sale feed (only `/cases/stats/market-index/latest-published-date`
and a current-only `/case/stats/municipality-average`). Hence DST EJ56. Because
a raw index ("2021 = 100") is hard to read, the chart **anchors** it: it scales
the index so the latest quarter equals today's actual median kr/m² for that
landsdel, giving an axis in kroner. That means the *trend* is DST's and the
*level* is our asking-price median — it's an estimate, and the caption says so.
`DST_LANDSDEL_MUNIS` maps landsdele 01/02/03 to our municipalities; 084 and 000
are excluded because they can't be anchored to our data.

**BVC spreadsheet.** Its 1992-onward sheets just duplicate DST. The unique value
is sheets `(C) Enfam.huse fra 1938 (realt)` and `(E) Ejerlejl. fra 1973 (realt)`
— inflation-adjusted, for København+Frederiksberg. Parsed with openpyxl and
resampled to one value per year. Sanity anchors: 2006 peak ≈ 191, 2012 trough
≈ 148, 2025 ≈ 314 (rebased to 2000 = 100).

**Two lessons paid for in bugs:**

1. *Wire every control.* The "Kun nær S-tog" checkbox lost its event listener in
   a refactor — `S.nearS` was declared and read by `filtered()` but nothing ever
   set it, so the filter silently did nothing for two releases. After touching
   `initUI()`, check every `id` in `index.html` has a listener. (`addrA`/`addrB`
   are bound dynamically via `$('#addr'+which)`, and `dstArea`/`indexMode`/
   `themeToggle` through local variables — those look unbound to a naive grep.)
2. *Never state a conclusion you haven't computed.* The size-vs-kr/m² chart once
   claimed a "stordriftsrabat". The data says the opposite: within each housing
   type kr/m² **rises** with size (condo r ≈ +0.23, villa r ≈ +0.20). The mixed
   cloud only looks flat because large homes are mostly villas, which sit at a
   lower kr/m² — a composition (Simpson) effect. The caption is now generated
   from the filtered data's actual correlation so it can't drift out of sync.

## Deploying

Push to `main`; GitHub Pages builds from `main` / root. Check status with:

```bash
gh api repos/Adamsinator/bolig-tracker/pages/builds/latest --jq .status
```

The daily Action commits to `main` too, so `git pull --rebase` before pushing if
you've been away.

## Per-machine setup (not in the repo)

- `gh auth login` — needed to push / inspect Pages builds
- Python 3 (+ `openpyxl` only if running the pipeline locally)
- `window.__MAP` is exposed in the browser console for poking at the Leaflet map,
  e.g. `__MAP.map.setView([55.8077,12.4685], 15)`
