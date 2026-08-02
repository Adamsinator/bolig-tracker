#!/usr/bin/env python3
"""Pull housing listings for the S-train corridor from boligsiden.dk's public API
and write two compact files consumed by the static site:

    data/listings.json  – one trimmed record per listing (~0.3 KB each)
    data/meta.json       – generated-at, counts, municipality names, stations

No API key, no auth. Dependency-free (stdlib only) so it runs locally and in CI.
"""
import base64
import contextlib
import gzip
import json
import math
import os
import re
import signal
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stations import STATIONS, LINES, LINE_LABELS  # noqa: E402

API = "https://api.boligsiden.dk/search/cases"
DAWA = "https://api.dataforsyningen.dk"
PER_PAGE = 500

# S-train corridor: København → Hillerød + the northern coast, near the S-train.
# slug -> (display name, official municipality code, for DAWA boundaries)
MUNICIPALITIES = {
    "koebenhavn":      ("København", 101),
    "frederiksberg":   ("Frederiksberg", 147),
    "gentofte":        ("Gentofte", 157),
    "lyngby-taarbaek": ("Lyngby-Taarbæk", 173),
    "rudersdal":       ("Rudersdal", 230),
    "gladsaxe":        ("Gladsaxe", 159),
    "furesoe":         ("Furesø", 190),
    "alleroed":        ("Allerød", 201),
    "hilleroed":       ("Hillerød", 219),
    "hoersholm":       ("Hørsholm", 223),
    "ballerup":        ("Ballerup", 151),
    "herlev":          ("Herlev", 163),
    "egedal":          ("Egedal", 240),
    "fredensborg":     ("Fredensborg", 210),
    # Region Hovedstaden, remainder (Bornholm excluded — different ferry/flight
    # market, not comparable comps). Codes cross-checked against DAWA jordstykker
    # while fetching data/grundareal.json — all 28 returned real parcel counts.
    "albertslund":     ("Albertslund", 165),
    "broendby":        ("Brøndby", 153),
    "dragoer":         ("Dragør", 155),
    "frederikssund":   ("Frederikssund", 250),
    "glostrup":        ("Glostrup", 161),
    "gribskov":        ("Gribskov", 270),
    "halsnaes":        ("Halsnæs", 260),
    "helsingoer":      ("Helsingør", 217),
    "hvidovre":        ("Hvidovre", 167),
    "hoeje-taastrup":  ("Høje-Taastrup", 169),
    "ishoej":          ("Ishøj", 183),
    "roedovre":        ("Rødovre", 175),
    "taarnby":         ("Tårnby", 185),
    "vallensbaek":     ("Vallensbæk", 187),
}
MUNI_NAME = {s: v[0] for s, v in MUNICIPALITIES.items()}
TYPES = ["condo", "villa"]  # the two addressTypes filters we query boligsiden with

# boligsiden's raw addressType → our house/apartment split. The two query filters
# don't line up with the real thing (the villa filter returns farms, hobby farms
# and fritidshuse; the condo filter returns rækkehuse), so classify on the raw
# value: anything you live in with your own ground is a "villa" (house), anything
# that is a flat in a larger building is a "condo" (lejlighed).
ADDR_TYPE_T = {
    "villa": "villa",
    "terraced house": "villa",      # rækkehus — an ordinary owner-occupied home
    "condo": "condo",
    "villa apartment": "condo",     # villalejlighed — a flat, not a house
}
# Dropped entirely: these aren't ordinary homes and belong to a different market.
# A landejendom/hobbylandbrug is priced on land and outbuildings, and a
# fritidshus (sommerhus) can't be a year-round residence — mixing them in would
# distort both the villa statistics and the fair-value model's comparables.
ADDR_TYPE_SKIP = {"farm", "hobby farm", "holiday house"}
# precise Danish label kept for display, so a rækkehus doesn't read as "Villa"
ADDR_TYPE_DA = {
    "villa": "Villa",
    "terraced house": "Rækkehus",
    "condo": "Ejerlejl.",
    "villa apartment": "Villalejl.",
}

# "Near the S-train" heuristic (metres, straight-line to nearest S-train station).
# 1 km ≈ a 12-min walk — a realistic catchment. (Metro uses a tighter 500 m on the
# client, since metro only serves dense central kommuner where almost everything is
# within ~1 km of a stop.)
STRAIN_NEAR_M = 1000


def fetch(muni, addr_type):
    """Yield every trimmed listing for one municipality + address type."""
    page = 1
    seen = 0
    while True:
        qs = urllib.parse.urlencode({
            "addressTypes": addr_type,
            "municipalities": muni,
            "per_page": PER_PAGE,
            "page": page,
        })
        req = urllib.request.Request(
            f"{API}?{qs}",
            headers={"Accept": "application/json", "User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"},
        )
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.load(r)
                break
            except Exception as e:  # transient network/5xx – back off and retry
                if attempt == 3:
                    raise
                print(f"    retry {muni}/{addr_type} p{page} ({e})", file=sys.stderr)
                time.sleep(2 * (attempt + 1))
        total = data.get("totalHits") or 0
        cases = data.get("cases") or []
        for c in cases:
            yield c
        seen += len(cases)
        if seen >= total or not cases:
            break
        page += 1
        time.sleep(0.3)


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# Station list used for the near-S-train distances and the map. Starts as the
# curated corridor list and is extended at runtime with the real S-tog stops
# fetched from OSM (Frederikssund, Favrholm, Høje Taastrup, Køge …), which the
# hardcoded list never covered.
ALL_STATIONS = list(STATIONS)


def nearest_station(lat, lon):
    """Return (name, corridor, dist_m, is_strain) for the closest rail station."""
    best = None
    for name, corridor, slat, slon, strain in ALL_STATIONS:
        d = haversine_m(lat, lon, slat, slon)
        if best is None or d < best[2]:
            best = (name, corridor, d, strain)
    return best


def address_line(addr):
    road = addr.get("roadName") or ""
    house = addr.get("houseNumber") or ""
    parts = [f"{road} {house}".strip()]
    fl = addr.get("floor")
    door = addr.get("door")
    if fl or door:
        parts.append(", ".join(x for x in [f"{fl}." if fl else "", door or ""] if x))
    return " ".join(p for p in parts if p).strip(", ").strip()


def floor_num(raw):
    """Map a Danish floor label to a sortable number (kl=-1, st=0, 1..n)."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("kl", "kld", "kælder", "k"):
        return -1
    if s in ("st", "stuen", "s"):
        return 0
    try:
        return int(s)
    except ValueError:
        return None


# Kommune land-reversion clauses (hjemfaldspligt / tilbagekøbsret) can slash a
# home's value — the agent must disclose them, so scan the whole case payload
# (schema-agnostic) for the tell-tale words.
_ENCUMBRANCE_RE = re.compile(r"hjemfald|tilbagek[øo]b|tilbagesk[øo]d", re.IGNORECASE)
def has_land_encumbrance(case):
    try:
        return bool(_ENCUMBRANCE_RE.search(json.dumps(case, ensure_ascii=False)))
    except Exception:
        return False


def trim(case, qtype=None):
    addr = case.get("address") or {}
    coords = case.get("coordinates") or {}
    lat, lon = coords.get("lat"), coords.get("lon")
    if lat is None or lon is None:
        return None
    raw_type = str(case.get("addressType") or "").strip().lower()
    if raw_type in ADDR_TYPE_SKIP:      # landejendom / hobbylandbrug / fritidshus
        return None
    st_name, st_corr, st_d, st_is = nearest_station(lat, lon)
    # nearest S-train specifically (may differ from overall nearest)
    strain_only = min(
        (s for s in ALL_STATIONS if s[4]),
        key=lambda s: haversine_m(lat, lon, s[2], s[3]),
    )
    strain_d = haversine_m(lat, lon, strain_only[2], strain_only[3])
    return {
        "id": case.get("caseID"),
        # House vs. apartment is decided by boligsiden's own addressType, mapped
        # through HOUSE_TYPES — the query filter alone isn't enough in either
        # direction: its villa filter also returns farms/fritidshuse, and its
        # condo filter returns rækkehuse. Unknown/missing types fall back to the
        # queried filter. "sub" keeps the precise Danish label for the card.
        "t": ADDR_TYPE_T.get(raw_type) or (qtype if qtype in ("condo", "villa") else "condo"),
        "sub": ADDR_TYPE_DA.get(raw_type),
        "p": case.get("priceCash"),
        "m2p": case.get("perAreaPrice"),
        "a": case.get("housingArea"),
        "lot": case.get("lotArea"),
        "r": case.get("numberOfRooms"),
        "d": case.get("daysOnMarket"),
        "chg": case.get("priceChangePercentage"),
        "y": case.get("yearBuilt"),
        "e": case.get("energyLabel"),
        "fl": addr.get("floor"),                       # etage label (condos)
        "fln": floor_num(addr.get("floor")),           # numeric floor for filtering
        "bsm": case.get("basementArea") or 0,          # kælder m²
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "muni": (addr.get("municipality") or {}).get("slug"),
        "city": addr.get("cityName"),
        "zip": addr.get("zipCode"),
        "adr": address_line(addr),
        "rt": (case.get("realtor") or {}).get("name"),
        "url": "https://www.boligsiden.dk/adresse/" + case["slug"] if case.get("slug") else case.get("caseUrl"),
        "elev": bool(case.get("hasElevator")),
        "balc": bool(case.get("hasBalcony")),
        # nearest rail station (any) + nearest S-train specifically
        "st": st_name,
        "sd": round(st_d),
        "sc": st_corr,
        "sst": round(strain_d),        # metres to nearest S-train station
        "ssn": strain_only[0],          # nearest S-train station name
        "near": strain_d <= STRAIN_NEAR_M,
        "hf": has_land_encumbrance(case),   # hjemfaldspligt / tilbagekøbsret disclosed
    }


# ---------------------------------------------------------------------------
# Municipality boundaries (Dataforsyningen / DAWA) — real land + coastline
# ---------------------------------------------------------------------------
def _rdp(points, eps):
    """Ramer–Douglas–Peucker line simplification (iterative)."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = points[a]
        bx, by = points[b]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1e-12
        dmax, idx = 0.0, -1
        for i in range(a + 1, b):
            px, py = points[i]
            d = abs((px - ax) * dy - (py - ay) * dx) / norm
            if d > dmax:
                dmax, idx = d, i
        if dmax > eps and idx != -1:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return [p for p, k in zip(points, keep) if k]


def _rdp_ring(points, eps):
    """RDP for a closed ring: split at the vertex farthest from the start so the
    baseline isn't degenerate, simplify both halves, then rejoin."""
    if len(points) < 4:
        return points
    x0, y0 = points[0]
    far = max(range(1, len(points)), key=lambda i: (points[i][0] - x0) ** 2 + (points[i][1] - y0) ** 2)
    a = _rdp(points[:far + 1], eps)
    b = _rdp(points[far:], eps)
    return a[:-1] + b            # drop shared vertex at the join


def fetch_boundaries():
    """Return {slug: {name, bbox, rings}} from DAWA, simplified for the web."""
    EPS = 0.00065          # ~45 m
    MIN_RING_PTS = 6
    MIN_RING_SPAN = 0.004  # drop islets smaller than ~300 m across
    geo = {}
    for slug, (name, code) in MUNICIPALITIES.items():
        url = f"{DAWA}/kommuner/{code}?format=geojson&srid=4326"
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                feat = json.load(r)
        except Exception as e:
            print(f"    boundary {name} failed: {e}", file=sys.stderr)
            continue
        g = feat.get("geometry") or {}
        polys = g.get("coordinates") or []
        if g.get("type") == "Polygon":
            polys = [polys]
        rings, mnx, mny, mxx, mxy = [], 1e9, 1e9, -1e9, -1e9
        for poly in polys:
            outer = poly[0] if poly else []          # outer ring only
            pts = [[round(x, 5), round(y, 5)] for x, y in outer]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            if not xs:
                continue
            span = max(max(xs) - min(xs), max(ys) - min(ys))
            simp = _rdp_ring(pts, EPS)
            if len(simp) < MIN_RING_PTS or span < MIN_RING_SPAN:
                continue
            rings.append(simp)
            mnx, mny = min(mnx, min(xs)), min(mny, min(ys))
            mxx, mxy = max(mxx, max(xs)), max(mxy, max(ys))
        if rings:
            geo[slug] = {"name": name, "bbox": [round(mnx, 5), round(mny, 5),
                         round(mxx, 5), round(mxy, 5)], "rings": rings}
            print(f"    boundary {name:16} rings={len(rings)} "
                  f"pts={sum(len(r) for r in rings)}")
    return geo


# ---------------------------------------------------------------------------
# Real long-run price history — Danmarks Statistik table EJ56 (quarterly index,
# 1992→present) for the landsdele covering this corridor, house vs condo.
# ---------------------------------------------------------------------------
DST_AREAS = {           # DST OMRÅDE id -> display name (corridor landsdele + context)
    "01":  "Byen København",
    "02":  "Københavns omegn",
    "03":  "Nordsjælland",
    "084": "Region Hovedstaden",
    "000": "Hele landet",
}
DST_CATS = {"0111": "villa", "2103": "condo"}   # Enfamiliehuse, Ejerlejligheder


def fetch_dst_index():
    """Pull EJ56 (price index, quarterly) and return a compact chart structure."""
    qs = urllib.parse.urlencode({
        "OMRÅDE": ",".join(DST_AREAS),
        "EJENDOMSKATE": ",".join(DST_CATS),
        "TAL": "100",            # Indeks
        "Tid": "*",
    })
    url = f"https://api.statbank.dk/v1/data/EJ56/JSONSTAT?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            d = json.load(r)
    except Exception as e:
        print(f"    DST EJ56 failed: {e}", file=sys.stderr)
        return None
    ds = d.get("dataset", d)
    dim = ds["dimension"]
    order = dim["id"]           # ['OMRÅDE','EJENDOMSKATE','TAL','ContentsCode','Tid']
    sizes = dim["size"]
    # index maps for each dimension
    def cat_index(name):
        idx = dim[name]["category"]["index"]
        return sorted(idx, key=lambda k: idx[k])
    areas = cat_index("OMRÅDE")
    cats = cat_index("EJENDOMSKATE")
    quarters = cat_index("Tid")
    values = ds["value"]
    # strides for row-major flattening in `order`
    stride = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        stride[i] = stride[i + 1] * sizes[i + 1]
    pos = {name: order.index(name) for name in ("OMRÅDE", "EJENDOMSKATE", "Tid")}
    series = {}
    for ai, a in enumerate(areas):
        for ci, c in enumerate(cats):
            key = f"{a}|{DST_CATS[c]}"
            row = []
            for ti in range(len(quarters)):
                flat = ai * stride[pos["OMRÅDE"]] + ci * stride[pos["EJENDOMSKATE"]] + ti * stride[pos["Tid"]]
                v = values[flat] if flat < len(values) else None
                row.append(v)
            series[key] = row
    print(f"    DST EJ56: {len(quarters)} quarters {quarters[0]}–{quarters[-1]}, "
          f"{len(series)} series")
    return {
        "source": "Danmarks Statistik · EJ56",
        "unit": "Prisindeks (2021 = 100)",
        "quarters": quarters,
        "areas": [{"id": a, "name": DST_AREAS[a]} for a in areas],
        "series": series,   # keyed "<areaId>|condo" / "<areaId>|villa"
    }


# ---------------------------------------------------------------------------
# Mortgage rates (realkreditrenter). Danmarks Nationalbank's statbank is the
# authoritative source and speaks the same PX-Web API as Danmarks Statistik.
# We can't reach it from the dev sandbox, so this first pass is a DISCOVERY
# step: list the rate-related tables and log their ids/dimensions from the CI
# run, then we build the real fetch against the confirmed table. Purely additive.
# ---------------------------------------------------------------------------
# Danmarks Statistik's statbank (known-reachable) also hosts Nationalbanken's
# DN* interest-rate tables, so we discover through it rather than NB's own host.
DST_API = "https://api.statbank.dk/v1"

def _dst_get(path):
    try:
        with hard_timeout(40), urllib.request.urlopen(f"{DST_API}/{path}", timeout=35) as r:
            return json.load(r)
    except Exception as ex:
        print(f"  mortgage: {path} failed ({ex})", file=sys.stderr)
        return None

# DNRNURI: new domestic mortgage lending — carries the effective rate incl. fees
# (datatype AL41/AL51EFFR) split by original rate-fixation, monthly back to 2003.
# The exact datatype/other dimension codes are resolved from tableinfo at runtime
# so the query is robust to dimensions we can't see from the sandbox.
MORTGAGE_TABLE = "DNRNURI"
RENTFIX_ORDER = [   # (code, short label) — filtered to what the table actually offers
    ("1M3M", "Variabel (≤3 mdr.)"),
    ("6M",   "Variabel (≤6 mdr.)"),
    ("1A",   "Kort rente (≤1 år)"),
    ("1A5A", "1–5 år"),
    ("10A",  "5–10 år"),
    ("S10A", "Fast (>10 år)"),
    ("ALLE", "Alle lån"),
]

def _pick_total(values):
    """The 'all/total' category id in a dimension (so we collapse dims we don't split on)."""
    for x in values:
        t = str(x.get("text") or "").lower()
        if t.startswith("alle") or "i alt" in t:
            return x.get("id")
    for tot in ("ALLE", "1000", "Z01", "A1", "1400"):
        if any(x.get("id") == tot for x in values):
            return tot
    return values[0].get("id") if values else None

def fetch_mortgage():
    info = _dst_get(f"tableinfo/{MORTGAGE_TABLE}?format=JSON")
    if not info:
        return None
    variables = info.get("variables") or []
    vmap = {v.get("id"): v for v in variables}
    dv = vmap.get("DATA") or vmap.get("DATATYPE")
    rf = vmap.get("RENTFIX")
    if not dv or not rf:
        print("  mortgage: DNRNURI missing DATA/RENTFIX dims", file=sys.stderr)
        return None
    eff = next((x["id"] for x in dv.get("values", [])
                if "effektiv" in str(x.get("text", "")).lower() and "rente" in str(x.get("text", "")).lower()), None)
    if not eff:
        print("  mortgage: no effective-rate datatype found", file=sys.stderr)
        return None
    rf_ids = {x["id"] for x in rf.get("values", [])}
    keep = [(c, lab) for c, lab in RENTFIX_ORDER if c in rf_ids] or \
           ([("ALLE", "Alle lån")] if "ALLE" in rf_ids else [])
    if not keep:
        return None
    # explicit target dims; every other dimension collapses to its total bucket
    params = {"DATA": eff, "RENTFIX": ",".join(c for c, _ in keep), "Tid": "*"}
    for v in variables:
        vid = v.get("id")
        if vid in ("DATA", "DATATYPE", "RENTFIX", "Tid"):
            continue
        params[vid] = _pick_total(v.get("values") or [])
    d = _dst_get(f"data/{MORTGAGE_TABLE}/JSONSTAT?{urllib.parse.urlencode(params)}")
    if not d:
        return None
    try:
        ds = d.get("dataset", d)
        dim = ds["dimension"]
        order = dim["id"]
        sizes = dim["size"]
        values = ds["value"]

        def cats(name):
            idx = dim[name]["category"]["index"]
            return sorted(idx, key=lambda k: idx[k])
        months = cats("Tid")
        rf_cats = cats("RENTFIX")
        stride = [1] * len(sizes)
        for i in range(len(sizes) - 2, -1, -1):
            stride[i] = stride[i + 1] * sizes[i + 1]
        p_rf, p_t = order.index("RENTFIX"), order.index("Tid")
        label_of = dict(keep)
        series = {}
        for rfi, rfc in enumerate(rf_cats):     # all other dims sit at index 0
            row = []
            for ti in range(len(months)):
                flat = rfi * stride[p_rf] + ti * stride[p_t]
                v = values[flat] if flat < len(values) else None
                row.append(round(v, 2) if isinstance(v, (int, float)) else None)
            series[label_of.get(rfc, rfc)] = row
    except Exception as ex:
        print(f"  mortgage: parse failed ({ex})", file=sys.stderr)
        return None
    latest = {}
    for lab, row in series.items():
        for mi in range(len(months) - 1, -1, -1):
            if row[mi] is not None:
                latest[lab] = {"month": months[mi], "rate": row[mi]}
                break
    print(f"  mortgage: {len(series)} fixation series, {len(months)} months "
          f"{months[0]}–{months[-1]}; latest Alle="
          f"{latest.get('Alle lån', {}).get('rate', '?')}%")
    return {"source": "Danmarks Nationalbank · DNRNURI (api.statbank.dk)",
            "unit": "Effektiv rente inkl. bidrag, % p.a. — nye realkreditlån",
            "months": months, "series": series, "latest": latest,
            "order": [lab for _, lab in keep]}


# ---------------------------------------------------------------------------
# Long real (inflation-adjusted) price index — Boligøkonomisk Videncenter.
# Houses back to 1938, condos back to 1973, for the Copenhagen area.
# ---------------------------------------------------------------------------
BVC_URL = "https://bvc.dk/media/scfgcxa2/bvc-boligprisindeks.xlsx"


def fetch_bvc():
    import io
    try:
        import openpyxl
    except ImportError:
        print("    BVC skipped (openpyxl not installed)", file=sys.stderr)
        return None
    try:
        req = urllib.request.Request(BVC_URL, headers={"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"})
        raw = urllib.request.urlopen(req, timeout=60).read()
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as e:
        print(f"    BVC fetch failed: {e}", file=sys.stderr)
        return None

    def col_by_prefix(header, prefix):
        for i, c in enumerate(header):
            if c and str(c).startswith(prefix):
                return i
        return None

    def annual(sheet, cols):
        ws = wb[sheet]
        rows = list(ws.iter_rows(values_only=True))
        header = rows[0]
        idx = {alias: col_by_prefix(header, pref) for pref, alias in cols.items()}
        by_year = {}
        for r in rows[1:]:
            if r[0] is None:
                continue
            try:
                y = int(float(r[0]))
            except (TypeError, ValueError):
                continue
            by_year[y] = {alias: (r[i] if i is not None else None) for alias, i in idx.items()}
        years = sorted(by_year)
        out = {"years": years}
        for alias in cols.values():
            out[alias] = [round(float(by_year[y][alias]), 1) if by_year[y][alias] is not None else None
                          for y in years]
        return out

    try:
        houses = annual("(C) Enfam.huse fra 1938 (realt)",
                        {"København+Frederiksberg": "kbhfrb", "Hele landet": "hele"})
        condos = annual("(E) Ejerlejl. fra 1973 (realt)",
                        {"KBH+FRB": "kbhfrb", "Hele landet": "hele"})
    except Exception as e:
        print(f"    BVC parse failed: {e}", file=sys.stderr)
        return None
    print(f"    BVC: houses {houses['years'][0]}–{houses['years'][-1]}, "
          f"condos {condos['years'][0]}–{condos['years'][-1]}")
    return {
        "source": "Boligøkonomisk Videncenter",
        "note": "Reale (inflationskorrigerede) prisindeks for København+Frederiksberg.",
        "houses": houses, "condos": condos,
    }


# ---------------------------------------------------------------------------
# History accumulation — a dated snapshot per (scope, type) appended each run
# ---------------------------------------------------------------------------
def snapshot(listings, date_str):
    def agg(rows):
        prices = [r["p"] for r in rows if r.get("p")]
        m2 = [r["m2p"] for r in rows if r.get("m2p")]
        days = [r["d"] for r in rows if r.get("d") is not None]
        cuts = sum(1 for r in rows if (r.get("chg") or 0) < 0)
        near_m2 = [r["m2p"] for r in rows if r.get("near") and r.get("m2p")]
        far_m2 = [r["m2p"] for r in rows if not r.get("near") and r.get("m2p")]
        if not rows:
            return None
        return {
            "n": len(rows),
            "medPrice": round(median(prices)) if prices else None,
            "medM2": round(median(m2)) if m2 else None,
            "medDays": round(median(days)) if days is not None and days else None,
            "pctCut": round(cuts / len(rows) * 100, 1),
            # distribution (so we can reconstruct the spread over time, not just
            # the median) and the S-tog premium — all forward-only enrichments
            "q1M2": _r(quantile(m2, 0.25)),
            "q3M2": _r(quantile(m2, 0.75)),
            "q1Price": _r(quantile(prices, 0.25)),
            "q3Price": _r(quantile(prices, 0.75)),
            "medM2Near": round(median(near_m2)) if near_m2 else None,
            "medM2Far": round(median(far_m2)) if far_m2 else None,
        }

    rows_out = []
    for t in TYPES:
        by_t = [r for r in listings if r["t"] == t]
        a = agg(by_t)
        if a:
            rows_out.append({"date": date_str, "scope": "all", "type": t, **a})
        for slug in MUNICIPALITIES:
            sub = [r for r in by_t if r["muni"] == slug]
            a = agg(sub)
            if a:
                rows_out.append({"date": date_str, "scope": slug, "type": t, **a})
    return rows_out


def median(arr):
    if not arr:
        return None
    a = sorted(arr)
    m = len(a) // 2
    return a[m] if len(a) % 2 else (a[m - 1] + a[m]) / 2


def quantile(arr, q):
    """Linear-interpolated quantile; None for empty input."""
    if not arr:
        return None
    a = sorted(arr)
    if len(a) == 1:
        return a[0]
    pos = (len(a) - 1) * q
    lo = int(pos)
    frac = pos - lo
    return a[lo] + (a[lo + 1] - a[lo]) * frac if lo + 1 < len(a) else a[lo]


def _r(v):
    return round(v) if v is not None else None


def merge_history(data_dir, new_rows, date_str, keep_days=3660):   # ~10 years
    path = os.path.join(data_dir, "history.json")
    series = []
    if os.path.exists(path):
        try:
            series = json.load(open(path, encoding="utf-8")).get("series", [])
        except Exception:
            series = []
    series = [r for r in series if r.get("date") != date_str]   # replace today
    series.extend(new_rows)
    dates = sorted({r["date"] for r in series})
    if len(dates) > keep_days:
        cutoff = set(dates[-keep_days:])
        series = [r for r in series if r["date"] in cutoff]
    series.sort(key=lambda r: (r["date"], r["scope"], r["type"]))
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"series": series}, f, ensure_ascii=False, separators=(",", ":"))
    return len({r["date"] for r in series})


# Per-listing change log — follow individual homes over time: price trajectory,
# first/last seen, and when a listing disappears (sold or withdrawn). Written to
# data/tracker.json. Removed listings are kept keep_removed_days (so recently
# sold homes stay visible) then pruned so the file doesn't grow without bound.
def track_listings(data_dir, listings, today, keep_removed_days=365):
    path = os.path.join(data_dir, "tracker.json")
    items = {}
    if os.path.exists(path):
        try:
            items = json.load(open(path, encoding="utf-8")).get("items", {})
        except Exception:
            items = {}

    cur = set()
    for r in listings:
        cid = r.get("id")
        if cid is None:
            continue
        cid = str(cid)
        cur.add(cid)
        p, m2, d = r.get("p"), r.get("m2p"), r.get("d")
        it = items.get(cid)
        if it is None:
            items[cid] = {
                "adr": r.get("adr"), "muni": r.get("muni"), "t": r.get("t"),
                "a": r.get("a"), "r": r.get("r"), "zip": r.get("zip"),
                "near": r.get("near"), "url": r.get("url"),
                "firstSeen": today, "lastSeen": today, "lastD": d, "removed": None,
                "events": [[today, p, m2]],
            }
        else:
            it["lastSeen"] = today
            it["lastD"] = d
            it["removed"] = None                       # still live (or reappeared)
            it["url"] = r.get("url") or it.get("url")
            ev = it.get("events") or []
            if not ev or ev[-1][1] != p:               # log only actual price moves
                ev.append([today, p, m2])
            it["events"] = ev

    for cid, it in items.items():                      # anything gone as of today
        if cid not in cur and it.get("removed") is None:
            it["removed"] = today

    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=keep_removed_days)).strftime("%Y-%m-%d")
    items = {cid: it for cid, it in items.items()
             if it.get("removed") is None or it["removed"] >= cutoff}

    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": len(items),
            "items": items,
        }, f, ensure_ascii=False, separators=(",", ":"))

    live = sum(1 for it in items.values() if it.get("removed") is None)
    changed = sum(1 for it in items.values() if len(it.get("events") or []) > 1)
    return {"tracked": len(items), "live": live, "withChanges": changed}


# ---------------------------------------------------------------------------
# Metro (M1–M4/Cityring) + Ring 3 letbane overlay, from OpenStreetMap via
# Overpass. Purely additive: any failure just means no overlay this build, so
# it never breaks the daily run. S-train logic (near/sst) is untouched.
# ---------------------------------------------------------------------------
OVERPASS_MIRRORS = ["https://overpass-api.de/api/interpreter",
                    "https://overpass.kumi.systems/api/interpreter",
                    # overpass.osm.jp dropped: fails TLS on every attempt
                    # (certificate doesn't match the hostname), not a transient
                    # blip — confirmed while probing the widened CORRIDOR_BBOX.
                    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
                    "https://overpass.private.coffee/api/interpreter"]
TRANSIT_BBOX = (55.55, 12.34, 55.86, 12.70)   # s, w, n, e — greater Copenhagen
# Wider box covering all tracked kommuner (Region Hovedstaden minus Bornholm):
# Dragør/Vallensbæk in the south, Halsnæs/Frederikssund in the west, Gribskov/
# Helsingør on the north coast. Used for rail geometry, POIs and geo-distance
# features, not per-kommune — one Overpass query per layer over this box.
CORRIDOR_BBOX = (55.55, 11.85, 56.15, 12.70)  # s, w, n, e

@contextlib.contextmanager
def hard_timeout(seconds):
    """Wall-clock cap via SIGALRM. urllib's socket timeout is per-recv, so a
    server that dribbles bytes slowly never trips it and can hang the build
    forever; SIGALRM interrupts the read no matter how the bytes arrive. POSIX
    + main thread only (both true in CI); elsewhere it's a harmless no-op."""
    try:
        old = signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(
            TimeoutError(f"hard timeout after {seconds}s")))
    except (ValueError, AttributeError):
        yield            # not the main thread / not POSIX — skip the guard
        return
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _overpass(query):
    """Run one Overpass query against the mirrors; return parsed JSON or None.
    Kept small so each concern (lines, stations) can fail independently. Both a
    socket timeout and a hard wall-clock cap apply so a hanging or slow-dribbling
    mirror fails over quickly — this overlay is optional and must never stall the
    run."""
    for attempt in range(2):   # two passes over the mirrors — Overpass is flaky
        for url in OVERPASS_MIRRORS:
            try:
                req = urllib.request.Request(url, data=query.encode("utf-8"),
                                             headers={"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"})
                with hard_timeout(95), urllib.request.urlopen(req, timeout=90) as r:
                    return json.load(r)
            except Exception as ex:
                print(f"  overpass via {url} failed ({ex})", file=sys.stderr)
        if attempt == 0:
            time.sleep(8)
    return None

def fetch_transit():
    s, w, n, e = TRANSIT_BBOX
    # Metro stations (station=subway). We drop light_rail stations/lines: in this
    # area those are Nærumbanen/Gribskovbanen local trains, not the metro or the
    # Ring 3 letbane, and were being mislabelled "Letbane".
    stations, seen = [], set()
    sdata = _overpass(
        f'[out:json][timeout:60];node["station"="subway"]({s},{w},{n},{e});out body;')
    for el in (sdata or {}).get("elements", []):
        tags = el.get("tags") or {}
        if el.get("type") == "node" and tags.get("name"):
            key = (round(el["lat"], 4), round(el["lon"], 4))
            if key not in seen:
                seen.add(key)
                stations.append({"name": tags["name"], "mode": "metro",
                                 "lat": round(el["lat"], 5), "lon": round(el["lon"], 5)})
    print(f"  transit: {len(stations)} metro stations fetched")

    # Metro lines from route=subway relations: one clean polyline per line ref
    # (M1–M4) taking a single direction's track, instead of drawing both physical
    # tracks of both directions as parallel lines.
    lines, seen_ref = [], set()
    ldata = _overpass(
        f'[out:json][timeout:90];rel["route"="subway"]({s},{w},{n},{e});out geom;')
    for r in (ldata or {}).get("elements", []):
        t = r.get("tags", {}) or {}
        ref = (t.get("ref") or "").strip()
        if not ref or ref in seen_ref:
            continue
        seen_ref.add(ref)
        segs = []
        for m in r.get("members", []):
            g = m.get("geometry")
            if m.get("type") != "way" or not g or len(g) < 2:
                continue
            segs.append(_rdp([[round(p["lat"], 5), round(p["lon"], 5)] for p in g], 0.0001))
        if segs:
            lines.append({"ref": ref, "mode": "metro",
                          "colour": t.get("colour") or t.get("color"), "segs": segs})
    n_metro = len(lines)

    # Local/regional lines (Nærumbanen, Gribskovbanen, Frederiksværkbanen, Lille
    # Nord, DSB Regionaltog…): route=light_rail relations that aren't S-tog, plus
    # route=train relations restricted to network="Takst Sjælland" — the tag
    # Lokaltog A/S's privatbaner and DSB's regional trains share, which also
    # happens to exclude exactly what should stay out: InterCity/Lyntog
    # (network=None) and the Swedish Skånetrafiken/SJ/Øresundståg relations that
    # clip the bbox near Helsingør (network="Skånetrafiken"/"SJ"/"Øresundståg").
    # Confirmed by probing OSM directly (#24 follow-up) rather than assumed.
    LOKAL_NAMES = {"910": "Nærumbanen", "L41": "Gribskovbanen"}
    s2, w2, n2, e2 = CORRIDOR_BBOX
    lok = _overpass(f'[out:json][timeout:90];rel["route"="light_rail"]({s2},{w2},{n2},{e2});out geom;')
    reg = _overpass(f'[out:json][timeout:90];rel["route"="train"]["network"="Takst Sjælland"]({s2},{w2},{n2},{e2});out geom;')
    seen_lokal = set()
    for r in (lok or {}).get("elements", []) + (reg or {}).get("elements", []):
        t = r.get("tags", {}) or {}
        name = t.get("name") or ""
        if "s-tog" in name.lower():
            continue
        ref = (t.get("ref") or "").strip()
        base = name.split(":")[0].strip() or ref
        if not base or base in seen_lokal:
            continue
        seen_lokal.add(base)
        segs = []
        for m in r.get("members", []):
            g = m.get("geometry")
            if m.get("type") != "way" or not g or len(g) < 2:
                continue
            segs.append(_rdp([[round(p["lat"], 5), round(p["lon"], 5)] for p in g], 0.00012))
        if segs:
            lines.append({"ref": ref or base, "mode": "lokal",
                          "name": LOKAL_NAMES.get(ref) or base,
                          "colour": t.get("colour") or t.get("color"), "segs": segs})

    if not lines and not stations:
        return None
    print(f"  transit: {n_metro} metro lines, {len(lines) - n_metro} lokalbaner "
          f"{[d.get('name', d['ref']) for d in lines if d['mode'] == 'lokal']}")
    return {"lines": lines, "stations": stations}


def _rdp(points, eps):
    """Ramer–Douglas–Peucker line simplification (iterative, no recursion limit).
    `points` is a list of [lat, lon]; `eps` is a tolerance in degrees (~0.00012 ≈
    13 m). Drops points that sit within `eps` of the chord, keeping the shape."""
    n = len(points)
    if n < 3:
        return points
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        ax, ay = points[i]
        bx, by = points[j]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1e-12
        dmax, idx = 0.0, -1
        for k in range(i + 1, j):
            px, py = points[k]
            d = abs((px - ax) * dy - (py - ay) * dx) / norm
            if d > dmax:
                dmax, idx = d, k
        if idx != -1 and dmax > eps:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(points, keep) if k]


def fetch_rail_geometry():
    """Real S-train track geometry (issue #6): OSM route relations so the lines
    follow the actual rails instead of straight hops between stations. Copenhagen
    S-tog is tagged route=light_rail (names 'S-tog A: …'); we keep those, group
    the member-way geometry by line ref (A/B/Bx/C/E/F/H), drop the duplicate
    other-direction relation, and simplify each way to keep the payload small.
    Fail-soft: any error returns None and the map falls back to station lines."""
    try:
        s, w, n, e = CORRIDOR_BBOX
        data = _overpass(f'[out:json][timeout:120];rel["route"="light_rail"]({s},{w},{n},{e});out geom;')
        rels = (data or {}).get("elements", [])
        by_ref, seen = {}, {}
        for r in rels:
            t = r.get("tags", {}) or {}
            name = t.get("name") or ""
            # Copenhagen S-tog is tagged route=light_rail with names 'S-tog A: …';
            # this also filters out the real Letbane / Nærumbanen (other names).
            if "s-tog" not in name.lower():
                continue
            ref = (t.get("ref") or "?").strip() or "?"
            seg_set = seen.setdefault(ref, set())
            segs = by_ref.setdefault(ref, [])
            for m in r.get("members", []):
                g = m.get("geometry")
                if m.get("type") != "way" or not g or len(g) < 2:
                    continue
                seg = [[round(p["lat"], 5), round(p["lon"], 5)] for p in g]
                key = (seg[0][0], seg[0][1], seg[-1][0], seg[-1][1], len(seg))
                if key in seg_set:      # skip the mirrored other-direction relation
                    continue
                seg_set.add(key)
                segs.append(_rdp(seg, 0.00012))
        # Kystbanen (regional coast line, Klampenborg → Helsingør) has no route
        # relation, but its rails are tagged railway=rail, name="Kystbanen".
        # Fetch the infrastructure and store it under the "kyst" key.
        kdata = _overpass(f'[out:json][timeout:120];way["railway"="rail"]["name"="Kystbanen"]({s},{w},{n},{e});out geom;')
        kyst, kseen = [], set()
        for el in (kdata or {}).get("elements", []):
            g = el.get("geometry")
            if el.get("type") == "way" and g and len(g) > 1:
                seg = [[round(p["lat"], 5), round(p["lon"], 5)] for p in g]
                key = (seg[0][0], seg[0][1], seg[-1][0], seg[-1][1], len(seg))
                if key in kseen:
                    continue
                kseen.add(key)
                kyst.append(_rdp(seg, 0.00012))
        if kyst:
            by_ref["kyst"] = kyst
        print(f"  rail: geometry by ref { {k: len(v) for k, v in by_ref.items()} }")
        return by_ref or None
    except Exception as ex:
        print(f"  rail geometry fetch failed ({ex})", file=sys.stderr)
        return None


def fetch_stog_stations(rail_geom):
    """Real S-tog stops from the OSM route relations, so the map isn't limited to
    the curated corridor list (which stopped at Hillerød and left Frederikssund,
    Favrholm, Høje Taastrup, Køge … off). Each stop is assigned to the line whose
    geometry runs closest. Also pulls stops on the region's other Takst Sjælland
    trains (Lokaltog A/S's Frederiksværkbanen/Lille Nord, DSB Regionaltog) —
    without these, a home near Gilleleje or Hundested had its "nearest station"
    computed against the closest S-tog stop instead, tens of km away (#24
    follow-up: those towns only entered the tracked region with the expansion).
    Fail-soft: returns [] on any error."""
    try:
        s, w, n, e = CORRIDOR_BBOX
        seen = {name.lower() for name, *_ in ALL_STATIONS}
        added = []

        data = _overpass(
            f'[out:json][timeout:120];rel["route"="light_rail"]["name"~"S-tog"]({s},{w},{n},{e});'
            f'node(r)->.n;.n out body;')
        found = []
        for el in (data or {}).get("elements", []):
            t = el.get("tags") or {}
            name = (t.get("name") or "").strip()
            if not name or el.get("type") != "node":
                continue
            # keep real stations/stops, skip level crossings and signals
            if not (t.get("railway") in ("station", "halt", "stop")
                    or t.get("public_transport") in ("station", "stop_position")):
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append((name, el["lat"], el["lon"]))
        # assign each new S-tog stop to the nearest line ref (for the map colour)
        for name, la, lo in found:
            best_ref, best_d = None, None
            for ref, segs in (rail_geom or {}).items():
                if ref == "kyst":
                    continue
                for seg in segs:
                    for plat, plon in seg:
                        d = (plat - la) ** 2 + ((plon - lo) * 0.56) ** 2
                        if best_d is None or d < best_d:
                            best_d, best_ref = d, ref
            # ~1.5 km cutoff so unrelated stations in the bbox aren't pulled in
            if best_ref and best_d is not None and best_d ** 0.5 < 0.015:
                added.append((name, best_ref, round(la, 5), round(lo, 5), True))
        n_stog = len(added)

        # Lokaltog A/S + DSB Regionaltog stops: no line-geometry match needed,
        # the network tag alone (same filter as the lokalbane fetch above)
        # already confirms these belong — most names duplicate existing S-tog/
        # Kystbanen stops (Nivå, Høje Taastrup…) and get skipped via `seen`.
        rdata = _overpass(
            f'[out:json][timeout:120];rel["route"="train"]["network"="Takst Sjælland"]({s},{w},{n},{e});'
            f'node(r)->.n;.n out body;')
        for el in (rdata or {}).get("elements", []):
            t = el.get("tags") or {}
            name = (t.get("name") or "").strip()
            if not name or el.get("type") != "node":
                continue
            if not (t.get("railway") in ("station", "halt", "stop")
                    or t.get("public_transport") in ("station", "stop_position")):
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            added.append((name, "lokal", round(el["lat"], 5), round(el["lon"], 5), False))
        print(f"  stations: +{n_stog} S-tog stops, +{len(added) - n_stog} regional/lokal stops from OSM "
              f"({', '.join(sorted(a[0] for a in added)[:12])}{' …' if len(added) > 12 else ''})")
        return added
    except Exception as ex:
        print(f"  station fetch failed ({ex})", file=sys.stderr)
        return []


POI_KINDS = [
    ("supermarket",  '["shop"="supermarket"]'),
    ("school",       '["amenity"="school"]'),
    ("kindergarten", '["amenity"="kindergarten"]'),
    ("childcare",    '["amenity"="childcare"]'),
]

def fetch_pois():
    """Everyday amenities (issue #7): supermarkets, schools, daycare across the
    corridor. Uses nwr + out center so area-mapped schools/daycare are included.
    Fail-soft: returns None on any error so the build never stalls on this."""
    try:
        s, w, n, e = CORRIDOR_BBOX
        body = "".join(f'nwr{sel}({s},{w},{n},{e});' for _, sel in POI_KINDS)
        data = _overpass(f'[out:json][timeout:120];({body});out center 8000;')
        els = (data or {}).get("elements", [])
        pois, counts = [], {}
        for el in els:
            t = el.get("tags") or {}
            if t.get("shop") == "supermarket":       kind = "supermarket"
            elif t.get("amenity") == "school":       kind = "school"
            elif t.get("amenity") == "kindergarten": kind = "kindergarten"
            elif t.get("amenity") == "childcare":    kind = "childcare"
            else:
                continue
            lat = el.get("lat") if el.get("lat") is not None else (el.get("center") or {}).get("lat")
            lon = el.get("lon") if el.get("lon") is not None else (el.get("center") or {}).get("lon")
            if lat is None or lon is None:
                continue
            pois.append({"n": t.get("name") or "", "k": kind,
                         "lat": round(lat, 5), "lon": round(lon, 5)})
            counts[kind] = counts.get(kind, 0) + 1
        print(f"  pois: {len(pois)} total by kind {counts}; named {sum(1 for p in pois if p['n'])}")
        return pois or None
    except Exception as ex:
        print(f"  pois fetch failed ({ex})", file=sys.stderr)
        return None


# boligsiden's *search* payload only mentions hjemfald/tilbagekøb for a few
# listings, so confirm the encumbrance on the ones that look suspiciously cheap
# for their area (that's where these clauses actually turn up) by scanning the
# public listing page. Bounded so we stay polite.
CHEAP_FRAC = 0.60      # < 60 % of the peer-group median kr/m² = worth a look
MAX_DETAIL = 300       # cap on detail fetches per build

def confirm_encumbrance(listings):
    groups = {}
    for r in listings:
        if r.get("m2p"):
            groups.setdefault((r["muni"], r["t"]), []).append(r["m2p"])
    med = {k: median(v) for k, v in groups.items() if v}
    cands = []
    for r in listings:
        m = med.get((r["muni"], r["t"]))
        if m and r.get("m2p") and r["m2p"] < CHEAP_FRAC * m and not r.get("hf") and r.get("url"):
            cands.append((r["m2p"] / m, r))
    cands.sort(key=lambda x: x[0])
    cands = [r for _, r in cands[:MAX_DETAIL]]
    found, done = 0, 0
    deadline = time.time() + 150       # overall budget for this optional step
    for r in cands:
        if time.time() > deadline:
            print(f"  detail-check budget reached at {done}/{len(cands)}")
            break
        try:
            req = urllib.request.Request(r["url"], headers={"User-Agent": "Mozilla/5.0 bolig-tracker/1.0"})
            with hard_timeout(15), urllib.request.urlopen(req, timeout=12) as resp:
                html = resp.read(500000).decode("utf-8", "ignore")
            if _ENCUMBRANCE_RE.search(html):
                r["hf"] = True
                found += 1
        except Exception:
            pass
        done += 1
        time.sleep(0.15)
    print(f"  detail-checked {done}/{len(cands)} cheap listings → +{found} hjemfald/tilbagekøb")


def annotate_metro(listings, transit):
    """Add nearest-metro distance (mst) and a combined near-rail flag.

    mst is measured against genuine Metro (subway) stations only. OSM's
    light_rail tagging around Copenhagen is unreliable — it mislabels many
    S-train stops (Holte, Farum, København H…) as light_rail — so folding
    those into a "metro" distance would be wrong. The letbane still rides
    along on the map overlay; it just doesn't drive the pricing signal."""
    pts = [(st["lat"], st["lon"]) for st in (transit or {}).get("stations", [])
           if st.get("mode") == "metro"]
    for r in listings:
        if pts:
            best = min(haversine_m(r["lat"], r["lon"], la, lo) for la, lo in pts)
            r["mst"] = round(best)
            r["nearRail"] = bool(r.get("near")) or best <= STRAIN_NEAR_M
        else:
            r["nearRail"] = bool(r.get("near"))


def merge_transit(new, prev):
    """The metro/letbane network is static, so a flaky Overpass fetch must never
    blank the overlay (or wipe mst, which breaks the metro premium/model). Keep
    whichever source actually has data for each component (stations, lines)."""
    new, prev = new or {}, prev or {}
    stations = new.get("stations") or prev.get("stations") or []
    lines = new.get("lines") or prev.get("lines") or []
    if not stations and not lines:
        return None
    src = []
    if not new.get("stations") and prev.get("stations"):
        src.append(f"kept {len(stations)} cached stations")
    if not new.get("lines") and prev.get("lines"):
        src.append(f"kept {len(lines)} cached line segments")
    if src:
        print("  transit: " + "; ".join(src))
    return {"lines": lines, "stations": stations}


# ---------------------------------------------------------------------------
# Realised sold prices from Boliga, which aggregates tinglysning (the land
# registry) and carries BBR attributes like build year and size. This gives a
# per-kommune × type median *realised* kr/m² for roughly the last two years and
# an asking-vs-sold gap — something the asking-price feed alone can't show.
# Purely additive: any failure just means no sold.json this build.
# ---------------------------------------------------------------------------
BOLIGA_SOLD = "https://api.boliga.dk/api/v2/sold/search/results"
BOLIGA_PTYPE = {"villa": 1, "condo": 3}   # Boliga propertyType codes
SOLD_MONTHS = 24
SOLD_RECENT_DAYS = 365                     # headline median = last 12 months
MAX_SOLD_PAGES = 25                        # hard cap per kommune × type

_LOT_KEYS = ("lotSize", "LotSize", "lotArea", "LotArea", "groundSize", "GroundSize",
             "plotSize", "PlotSize", "grundareal", "Grundareal")
_lot_seen = [0, 0]                # [records with a lot, records without]


def _sold_lot(s):
    """Lot area off a Boliga sold record. The field name isn't documented and
    isn't in every payload, so try the plausible spellings and count how often
    we actually get one — a comps match on lot is only worth doing if the data
    is really there."""
    for k in _LOT_KEYS:
        v = s.get(k)
        if v:
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if 10 <= v <= 100000:
                _lot_seen[0] += 1
                return int(v)
    _lot_seen[1] += 1
    return None


def _boliga_sold_page(code, ptype, date_min, page):
    qs = urllib.parse.urlencode({
        "municipality": code, "propertyType": ptype, "salesDateMin": date_min,
        "pageSize": 500, "page": page, "sort": "date-d",
    })
    for attempt in range(5):          # ~2+4+6+8s of backoff — rides out rate limits
        try:
            req = urllib.request.Request(f"{BOLIGA_SOLD}?{qs}",
                headers={"Accept": "application/json", "User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"})
            with hard_timeout(40), urllib.request.urlopen(req, timeout=35) as r:
                return json.load(r)
        except Exception as ex:
            if attempt == 4:
                print(f"  sold fetch {code}/{ptype} p{page} failed ({ex})", file=sys.stderr)
                return None
            time.sleep(2 * (attempt + 1))
    return None

def _quarter(iso):
    try:
        return f"{int(iso[:4])}Q{(int(iso[5:7]) - 1) // 3 + 1}"
    except Exception:
        return None

def _saletype_bucket(raw):
    s = str(raw or "").lower()
    if "fam" in s:
        return "familie"
    if "auktion" in s or "auction" in s:
        return "auktion"
    if "alm" in s or "fri" in s or "regular" in s or "normal" in s or s in ("", "1"):
        return "almindelig"
    return "andet"

def fetch_sold(listings):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=SOLD_MONTHS * 31)
    recent_cut = now - timedelta(days=SOLD_RECENT_DAYS)
    date_min = cutoff.strftime("%Y-%m-%d")
    by_muni, series = {}, {}
    by_decade = {"condo": {}, "villa": {}}   # BBR build-decade -> realised kr/m² (recent window)
    saletypes = {}                           # market composition over the recent window
    points = []                              # (lat, lon, m2p, type) for per-listing local comps
    all_q = {"condo": {}, "villa": {}}       # corridor-pooled kr/m² per quarter (all sales, per type)
    logged_fields = False
    total = 0

    def scan_group(code, ptype, t):
        """Page one kommune×type. Also folds BBR build-year into by_decade and the
        sale-type mix into saletypes (shared). Returns per-group aggregates."""
        nonlocal logged_fields
        rp, rm, rsize, ryear, by_q, rows_seen, failed = [], [], [], [], {}, 0, False
        for page in range(1, MAX_SOLD_PAGES + 1):
            data = _boliga_sold_page(code, ptype, date_min, page)
            if not data:
                failed = (page == 1)      # page-1 miss = we got nothing at all
                break
            results = data.get("results") or data.get("Results") or []
            if not logged_fields and results:
                print(f"  boliga sold fields: {sorted(results[0].keys())}")
                logged_fields = True
            if not results:
                break
            stop = False
            for s in results:
                sd = str(s.get("soldDate") or s.get("SoldDate") or "")
                try:
                    d = datetime.fromisoformat(sd[:10]).replace(tzinfo=timezone.utc)
                except Exception:
                    d = None
                recent = bool(d and d >= recent_cut)
                bucket = _saletype_bucket(s.get("saleType") or s.get("SaleType"))
                if recent:               # market composition counts every sale type
                    saletypes[bucket] = saletypes.get(bucket, 0) + 1
                if bucket in ("familie", "auktion"):
                    continue             # price aggregates use arm's-length sales only
                m2 = s.get("sqmPrice") or s.get("SqmPrice")
                price = s.get("price") or s.get("Price")
                yr = s.get("buildYear") or s.get("BuildYear")
                sz = s.get("size") or s.get("Size")
                q = _quarter(sd)
                # drop garbage kr/m² (tiny units, data errors) so a few bad records
                # can't poison local comps or the medians
                if m2 and 5000 <= m2 <= 200000:
                    if q:
                        by_q.setdefault(q, []).append(m2)
                        all_q[t].setdefault(q, []).append(m2)   # corridor pool
                    if recent:
                        rm.append(m2)
                        if price and price > 0:
                            rp.append(price)
                        if sz and sz > 0:
                            rsize.append(sz)
                        if yr and 1800 < yr <= now.year:
                            ryear.append(yr)
                            by_decade[t].setdefault((int(yr) // 10) * 10, []).append(m2)
                        la = s.get("latitude") or s.get("Latitude")
                        lo = s.get("longitude") or s.get("Longitude")
                        if la and lo:
                            # carry quarter/size/year/lot so comps can be time-adjusted
                            # and matched on similarity, not just location + type
                            points.append((round(la, 5), round(lo, 5), m2, t, q,
                                           sz if sz and sz > 0 else None,
                                           int(yr) if yr and 1800 < yr <= now.year else None,
                                           _sold_lot(s)))
                if d and d < cutoff:
                    stop = True
            rows_seen += len(results)
            meta_total = (data.get("meta") or {}).get("totalCount")
            if stop or (meta_total and page * 500 >= meta_total):
                break
            time.sleep(0.25)
        return rp, rm, rsize, ryear, by_q, rows_seen, failed

    for slug, (name, code) in MUNICIPALITIES.items():
        for t, ptype in BOLIGA_PTYPE.items():
            rp, rm, rsize, ryear, by_q, rows_seen, failed = scan_group(code, ptype, t)
            total += rows_seen
            if failed:                    # got nothing at all — one more try after a pause
                print(f"  sold {name}/{t}: page-1 fetch failed, retrying once…", file=sys.stderr)
                time.sleep(4)
                rp, rm, rsize, ryear, by_q, rows_seen, failed = scan_group(code, ptype, t)
                total += rows_seen
            if rm:
                by_muni.setdefault(slug, {})[t] = {
                    "n": len(rm),
                    "medPrice": round(median(rp)) if rp else None,
                    "medM2": round(median(rm)),
                    "medSize": round(median(rsize)) if rsize else None,
                    "medYear": round(median(ryear)) if ryear else None,
                }
            if by_q:
                series.setdefault(slug, {})[t] = {q: round(median(v)) for q, v in by_q.items()}
            flag = " (FETCH FAILED)" if failed else (" (no sales in window)" if not rm else "")
            print(f"  sold {name:16} {t:6} {len(rm)} recent sales{flag}")
    if not by_muni:
        return None
    # asking-vs-sold gap: current median asking kr/m² vs recent median realised kr/m²
    ask = {}
    for r in listings:
        if r.get("m2p") and r.get("muni") and r.get("t"):
            ask.setdefault((r["muni"], r["t"]), []).append(r["m2p"])
    gap = {}
    for slug, per in by_muni.items():
        for t, agg in per.items():
            a = ask.get((slug, t))
            if a and agg.get("medM2"):
                am = median(a)
                gap.setdefault(slug, {})[t] = {"askM2": round(am), "soldM2": agg["medM2"],
                                               "gapPct": round((am / agg["medM2"] - 1) * 100)}
    # realised kr/m² by build-decade (BBR), per type — needs enough sales to be stable
    decades = {}
    for t in ("condo", "villa"):
        dd = {str(dec): round(median(v)) for dec, v in by_decade[t].items() if len(v) >= 25}
        if dd:
            decades[t] = dd
    tot_st = sum(saletypes.values())
    sale_mix = {"counts": saletypes,
                "pct": {k: round(v / tot_st * 100, 1) for k, v in saletypes.items()}} if tot_st else None
    quarters = sorted({q for per in series.values() for s in per.values() for q in s})
    # corridor-pooled quarterly median (all sales, per type) — the honest
    # corridor trend. Median-of-kommune-medians would over-weight the many cheap
    # suburban condos and wrongly put villa kr/m² above condo.
    series_all = {t: {q: round(median(v)) for q, v in qs.items() if len(v) >= 15}
                  for t, qs in all_q.items()}
    series_all = {t: qm for t, qm in series_all.items() if qm}
    print(f"  sold: {total} rows scanned, {len(by_muni)} kommuner; "
          f"decades condo={len(decades.get('condo', {}))} villa={len(decades.get('villa', {}))}; "
          f"sale-mix {saletypes}")
    return {"generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "windowMonths": SOLD_MONTHS,
            "recentDays": SOLD_RECENT_DAYS, "byMuni": by_muni, "askingVsSold": gap,
            "quarters": quarters, "series": series, "seriesAll": series_all,
            "byDecade": decades, "saleMix": sale_mix,
            "points": points}   # popped before writing sold.json — used for per-listing comps


# Attach each listing's local realised benchmark: nearby arm's-length sales of
# the same type, drawn from tinglysning. Three things make the benchmark sharper
# than a plain radius median (issue #20):
#   • each sale is time-adjusted to today with the corridor's own quarterly
#     medians, so a 23-month-old sale no longer counts like last month's,
#   • comps are matched on living area and build year, because kr/m² falls
#     systematically with size and a 45 m² flat is no benchmark for a 140 m² one,
#   • the nearest K are distance-weighted rather than a hard radius cut-off, so a
#     sparse area degrades gracefully instead of jumping to a 2 km median.
# The spread of the comps is stored too, so thin/noisy benchmarks can be told
# apart from tight ones.
COMP_CELL = 0.01                 # ~0.6–1.1 km grid cell for fast neighbour lookup
COMP_MAX_R = 2000                # metres — outer bound on how far we will look
COMP_MIN = 6                     # fewer than this and we don't claim a benchmark
COMP_K = 25                      # use the nearest K matched sales
COMP_AREA_TOL = 0.30             # ±30 % living area
COMP_YEAR_TOL = 20               # ±20 years build year
COMP_LOT_TOL = 0.40              # ±40 % lot area when matching houses
COMP_HALF = 500.0                # metres — distance at which a comp's weight halves


def _weighted_median(pairs):
    """pairs = [(value, weight)] → the weighted median value."""
    if not pairs:
        return None
    pairs = sorted(pairs)
    half = sum(w for _, w in pairs) / 2.0
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= half:
            return v
    return pairs[-1][0]


COMP_CHAIN_MIN = 6               # kommuner that must span a quarter step to trust it


def _time_factors(series_all, series_by_muni=None):
    """Multiplier that lifts a quarter's kr/m² onto today's level, per type.

    Chained on the kommuner present in *both* quarters of each step, rather than
    read off the corridor-pooled median. The pooled median moves whenever the mix
    of kommuner reporting changes, and the oldest quarter is systematically thin
    because MAX_SOLD_PAGES truncates the highest-volume kommuner first — sorted
    newest-first, so it is always the oldest quarter that loses them. In one
    build that dropped København out of the oldest condo quarter entirely and
    turned a real +22 % into an apparent +66 %, which then inflated every
    older comp and made listings look far cheaper than they were.

    Falls back to the pooled series when there aren't enough overlapping
    kommuner to chain."""
    factors = {}
    for t, qm in (series_all or {}).items():
        if not qm:
            continue
        qs = sorted(qm)
        level, chained = {qs[0]: 1.0}, True
        for a, b in zip(qs, qs[1:]):
            ratios = []
            for m in (series_by_muni or {}).values():
                s = (m.get(t) or {})
                if s.get(a) and s.get(b):
                    ratios.append(s[b] / s[a])
            if len(ratios) < COMP_CHAIN_MIN:
                chained = False
                break
            level[b] = level[a] * statistics.median(ratios)
        if not chained:
            latest = max(qm)
            if qm.get(latest):
                factors[t] = {q: qm[latest] / v for q, v in qm.items() if v}
                print(f"    comps: {t} time index falls back to pooled medians "
                      f"(too few kommuner span every quarter)", file=sys.stderr)
            continue
        base = level[qs[-1]]
        factors[t] = {q: base / v for q, v in level.items() if v}
        pooled = qm[qs[-1]] / qm[qs[0]] if qm.get(qs[0]) else None
        print(f"    comps: {t} index {qs[0]}→{qs[-1]} {(base/level[qs[0]]-1)*100:+.1f} %"
              + (f" (pooled medians would say {(pooled-1)*100:+.1f} %)" if pooled else ""))
    return factors


def annotate_comps(listings, points, series_all=None, series_by_muni=None, grundareal=None):
    if not points:
        return 0
    factors = _time_factors(series_all, series_by_muni)
    if factors:
        print(f"    comps: time-adjusting via corridor quarterly medians "
              f"({', '.join(f'{t}:{len(f)}q' for t, f in factors.items())})")
    area_of = _grundareal_lookup(grundareal)
    lot_from_grid = 0
    if area_of:
        # Resolve once per sold point rather than once per (listing, candidate)
        # pair — the same point is re-examined by every nearby listing, so
        # doing this inside that loop would look up the same coordinate
        # hundreds of times over and make the "how many did we backfill"
        # count meaningless.
        filled = []
        for p in points:
            if len(p) > 7 and p[7]:
                filled.append(p)
                continue
            a = area_of(p[0], p[1])
            if a:
                lot_from_grid += 1
            filled.append((*p[:7], a) if len(p) > 7 else (*p, a))
        points = filled
        print(f"    comps: grundareal grid filled {lot_from_grid}/{len(points)} sold points "
              f"that had no lot size from boliga")
    grid = {}
    for p in points:
        grid.setdefault((int(p[0] / COMP_CELL), int(p[1] / COMP_CELL)), []).append(p)
    done = adjusted = 0
    lot_matched = [0]
    for r in listings:
        la, lo, t = r.get("lat"), r.get("lon"), r.get("t")
        if la is None or lo is None:
            continue
        ci, cj = int(la / COMP_CELL), int(lo / COMP_CELL)
        ftab = factors.get(t, {})
        cand = []
        for di in range(-4, 5):        # ±4 cells covers the 2 km bound at 55°N
            for dj in range(-4, 5):
                for p in grid.get((ci + di, cj + dj), ()):
                    if p[3] != t:
                        continue
                    d = haversine_m(la, lo, p[0], p[1])
                    if d > COMP_MAX_R:
                        continue
                    q, sz, yr = (p[4], p[5], p[6]) if len(p) > 6 else (None, None, None)
                    plot = p[7] if len(p) > 7 else None
                    cand.append((d, p[2] * ftab.get(q, 1.0), sz, yr, plot))
        if not cand:
            continue
        # prefer sales of a similar size and age; fall back to all nearby sales
        # of the same type when that leaves too few to be meaningful
        a, y, lot = r.get("a"), r.get("y"), r.get("lot")
        near = cand
        if a:
            lo_a, hi_a = a * (1 - COMP_AREA_TOL), a * (1 + COMP_AREA_TOL)
            sim = [c for c in cand
                   if c[2] and lo_a <= c[2] <= hi_a
                   and (not y or not c[3] or abs(c[3] - y) <= COMP_YEAR_TOL)]
            # For a house the plot is a big part of the price, so narrow further
            # on lot size when both sides have it — but only if enough comps
            # survive, otherwise a matched-on-everything sample of three is worse
            # than a looser sample of fifteen.
            if t == "villa" and lot:
                lo_l, hi_l = lot * (1 - COMP_LOT_TOL), lot * (1 + COMP_LOT_TOL)
                tight = [c for c in sim if c[4] and lo_l <= c[4] <= hi_l]
                if len(tight) >= COMP_MIN:
                    sim = tight
                    lot_matched[0] += 1
            if len(sim) >= COMP_MIN:
                near = sim
                adjusted += 1
        near.sort(key=lambda c: c[0])
        near = near[:COMP_K]
        if len(near) < COMP_MIN:
            continue
        vals = [(c[1], 1.0 / (1.0 + c[0] / COMP_HALF)) for c in near]
        m2 = _weighted_median(vals)
        if not m2:
            continue
        srt = sorted(v for v, _ in vals)
        q1, q3 = srt[len(srt) // 4], srt[(3 * len(srt)) // 4]
        r["cmpM2"] = round(m2)
        r["cmpN"] = len(near)
        r["cmpR"] = int(near[-1][0])          # how far the farthest used comp sits
        r["cmpS"] = round((q3 - q1) / m2 * 100)   # spread as % of the benchmark
        done += 1
    print(f"    comps: {adjusted}/{done} used size/age-matched sales"
          f" · {lot_matched[0]} of those also matched on lot size")
    if _lot_seen[0] or _lot_seen[1]:
        tot = _lot_seen[0] + _lot_seen[1]
        print(f"    comps: boliga gave a lot size on {_lot_seen[0]}/{tot} sold records"
              f" ({_lot_seen[0]*100//max(tot,1)} %)")
    return done


def fetch_geo_features():
    """Location quality for the fair-value model: how close a home is to a
    motorway (noise — a negative), to open water, and to forest/park. Each layer
    is reduced to sample points along its geometry; only the per-listing
    distances are kept, so nothing heavy ships to the client. Fail-soft: a layer
    that fails to fetch is simply left out."""
    s, w, n, e = CORRIDOR_BBOX
    layers = {
        # motorways and trunk roads — the noisy ones you don't want to back onto
        "mot": f'way["highway"~"^(motorway|trunk)$"]({s},{w},{n},{e});',
        # The coast is its own thing: first row on Strandvejen carries a premium
        # nothing inland matches, and pooling it with lakes made Øresund and
        # Bagsværd Sø the same feature. Kept separate so the model can price them
        # differently — "wat" stays for builds whose data predates the split.
        "sea": f'way["natural"="coastline"]({s},{w},{n},{e});',
        "lak": f'way["natural"="water"]["name"]({s},{w},{n},{e});',
        # forest, woodland and parks
        "grn": (f'way["landuse"="forest"]({s},{w},{n},{e});'
                f'way["natural"="wood"]({s},{w},{n},{e});'
                f'way["leisure"="park"]({s},{w},{n},{e});'),
    }
    geo = {}
    for key, body in layers.items():
        try:
            data = _overpass(f'[out:json][timeout:120];({body});out geom;')
            pts = []
            for el in (data or {}).get("elements", []):
                g = el.get("geometry") or []
                # every 3rd node is ~100 m resolution — plenty for a distance feature
                for p in g[::3]:
                    pts.append((p["lat"], p["lon"]))
            if pts:
                geo[key] = pts
            print(f"  geo {key}: {len(pts)} sample points")
        except Exception as ex:
            print(f"  geo {key} fetch failed ({ex})", file=sys.stderr)
    return geo or None


def annotate_geo_distance(listings, geo):
    """Add r['geo'] = {'mot','wat','grn'} — metres to the nearest motorway, open
    water and green area. Same coarse-grid prefilter as the amenity distances."""
    if not geo:
        return
    CELL = 0.02
    grids, allpts = {}, {}
    for k, pts in geo.items():
        g = {}
        for la, lo in pts:
            g.setdefault((int(la / CELL), int(lo / CELL)), []).append((la, lo))
        grids[k], allpts[k] = g, pts

    def nearest(lat, lon, k):
        clat = math.cos(math.radians(lat))
        ci, cj = int(lat / CELL), int(lon / CELL)
        cand = []
        rng = 2
        while not cand and rng <= 6:      # widen the search for sparse layers
            cand = [p for di in range(-rng, rng + 1) for dj in range(-rng, rng + 1)
                    for p in grids[k].get((ci + di, cj + dj), [])]
            rng += 2
        if not cand:
            cand = allpts[k]
        best = None
        for a, b in cand:
            dy = (a - lat) * 110540.0
            dx = (b - lon) * 111320.0 * clat
            d = dx * dx + dy * dy
            if best is None or d < best:
                best = d
        return int(best ** 0.5) if best is not None else None

    n = 0
    for r in listings:
        lat, lon = r.get("lat"), r.get("lon")
        if lat is None or lon is None:
            continue
        gd = {}
        for k in geo:
            d = nearest(lat, lon, k)
            if d is not None:
                gd[k] = d
        if gd:
            r["geo"] = gd
            n += 1
    print(f"  geo distances: annotated {n}/{len(listings)} listings")


def annotate_poi_distance(listings, pois):
    """Issue #7: add each listing's straight-line distance (metres) to the nearest
    supermarket / school / daycare, from data/poi.json, as r['poi']={'sup','sch',
    'day'}. Uses a coarse grid prefilter for speed, falling back to all points.
    Fail-soft: no pois → no-op."""
    if not pois:
        return
    kmap = {"supermarket": "sup", "school": "sch", "kindergarten": "day", "childcare": "day"}
    CELL = 0.02   # ~2.2 km latitude cells
    grids = {"sup": {}, "sch": {}, "day": {}}
    allpts = {"sup": [], "sch": [], "day": []}
    for p in pois:
        k = kmap.get(p.get("k"))
        if not k:
            continue
        la, lo = p.get("lat"), p.get("lon")
        if la is None or lo is None:
            continue
        grids[k].setdefault((int(la / CELL), int(lo / CELL)), []).append((la, lo))
        allpts[k].append((la, lo))

    def nearest(lat, lon, k):
        clat = math.cos(math.radians(lat))
        ci, cj = int(lat / CELL), int(lon / CELL)
        cand = []
        for di in range(-2, 3):
            for dj in range(-2, 3):
                cand += grids[k].get((ci + di, cj + dj), [])
        if not cand:
            cand = allpts[k]
        best = None
        for a, b in cand:
            dy = (a - lat) * 110540.0
            dx = (b - lon) * 111320.0 * clat
            d = dx * dx + dy * dy
            if best is None or d < best:
                best = d
        return int(best ** 0.5) if best is not None else None

    n = 0
    for r in listings:
        lat, lon = r.get("lat"), r.get("lon")
        if lat is None or lon is None:
            continue
        pd = {}
        for k in ("sup", "sch", "day"):
            d = nearest(lat, lon, k)
            if d is not None:
                pd[k] = d
        if pd:
            r["poi"] = pd
            n += 1
    print(f"  poi distances: annotated {n}/{len(listings)} listings")


# Miljøstyrelsen maps road noise in 5 dB bands from 55 dB Lden upward. Anything
# outside every band is quieter than the lowest mapped one; 50 is a reasonable
# stand-in so "not in a band" doesn't read as "no data".
NOISE_FLOOR_DB = 50


def annotate_noise(listings, noise):
    """Add r['db'] — road-traffic noise (Lden, façade level) from Miljøstyrelsen's
    2022 EU noise mapping, in dB.

    data/noise.json holds a gzipped byte grid built once by fetch_noise_once.py:
    one dB reading per ~20 m cell, so this is a straight index rather than a
    point-in-polygon search. A 0 cell means no mapped band, i.e. quieter than the
    55 dB floor the mapping starts at — that becomes NOISE_FLOOR_DB. Fail-soft:
    no grid → no-op, and the model treats a missing r['db'] as "unknown" rather
    than "quiet"."""
    if not noise or not noise.get("data"):
        return
    try:
        grid = gzip.decompress(base64.b64decode(noise["data"]))
    except Exception as ex:
        print(f"  noise: could not decode the grid ({ex})", file=sys.stderr)
        return
    cols, rows = int(noise["cols"]), int(noise["rows"])
    if len(grid) != cols * rows:
        print(f"  noise: grid is {len(grid)} bytes, expected {cols*rows} — skipping",
              file=sys.stderr)
        return
    w, s, e, n_ = (float(v) for v in noise["bbox"])

    hit = 0
    for r in listings:
        lat, lon = r.get("lat"), r.get("lon")
        if lat is None or lon is None:
            continue
        if not (w <= lon <= e and s <= lat <= n_):
            continue                     # outside the mapped corridor: leave unknown
        col = min(cols - 1, int((lon - w) / (e - w) * cols))
        row = min(rows - 1, int((n_ - lat) / (n_ - s) * rows))
        v = grid[row * cols + col]
        r["db"] = int(v) if v else NOISE_FLOOR_DB
        hit += 1
    loud = sum(1 for r in listings if (r.get("db") or 0) >= 58)
    print(f"  noise: {cols}×{rows} grid, annotated {hit}/{len(listings)} listings "
          f"({loud} at 58 dB or above)")


def _read_grid(doc, label):
    """Decode one of the gzipped byte grids (noise, parcel rows) and return
    (grid, cols, rows, bbox) — or None if it doesn't check out."""
    if not doc or not doc.get("data"):
        return None
    try:
        grid = gzip.decompress(base64.b64decode(doc["data"]))
    except Exception as ex:
        print(f"  {label}: could not decode the grid ({ex})", file=sys.stderr)
        return None
    cols, rows = int(doc["cols"]), int(doc["rows"])
    if len(grid) != cols * rows:
        print(f"  {label}: grid is {len(grid)} bytes, expected {cols*rows} — skipping",
              file=sys.stderr)
        return None
    return grid, cols, rows, tuple(float(v) for v in doc["bbox"])


def _grundareal_lookup(doc):
    """Build a nearest-point area lookup from data/grundareal.json — a flat
    [lat, lon, logarea] array covering every matrikel in the region, fetched
    once by fetch_parcel_areas_once.py. Returns a f(lat, lon) -> area_m2 (or
    None) closure using the same coarse spatial hash as annotate_comps/
    annotate_geo_distance, or None if the file isn't there.

    This is a point lookup, not a polygon match, so a coordinate right on a
    parcel boundary can resolve to the neighbouring parcel. Fine for a ±40%
    comp-matching tolerance; would not be fine for anything needing the exact
    parcel."""
    if not doc or not doc.get("data"):
        return None
    try:
        flat = json.loads(gzip.decompress(base64.b64decode(doc["data"])))
    except Exception as ex:
        print(f"  grundareal: could not decode ({ex})", file=sys.stderr)
        return None
    scale = float(doc.get("logScale", 20.0))
    CELL = 0.01
    grid = {}
    for i in range(0, len(flat) - 2, 3):
        la, lo, logv = flat[i], flat[i + 1], flat[i + 2]
        grid.setdefault((int(la / CELL), int(lo / CELL)), []).append((la, lo, logv))
    print(f"  grundareal: {len(flat)//3} parcels loaded")

    def lookup(lat, lon):
        ci, cj = int(lat / CELL), int(lon / CELL)
        best, best_d = None, None
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for la, lo, logv in grid.get((ci + di, cj + dj), ()):
                    d = (la - lat) ** 2 + (lo - lon) ** 2
                    if best_d is None or d < best_d:
                        best_d, best = d, logv
        if best is None:
            return None
        return round(2 ** (best / scale))

    return lookup


def annotate_parcel_row(listings, doc):
    """Add r['row'] — how many matrikler lie between the home and the water.

    0 means the home's own parcel borders the coastline: first row, by cadastral
    fact rather than by proximity. This is what distance to water cannot say —
    a house 60 m back with the sea at the end of its garden and one 60 m back
    behind a neighbour are the same distance and very different homes.

    data/parcelrow.json stores row+1 per cell on the same grid geometry as the
    noise map, so this is an index lookup. A 0 cell means no parcel was mapped
    there, which is the normal case away from the coast — those listings simply
    get no 'row' rather than a made-up one. Fail-soft: no grid → no-op."""
    got = _read_grid(doc, "parcel rows")
    if not got:
        return
    grid, cols, rows, (w, s, e, n_) = got
    hit = 0
    for r in listings:
        lat, lon = r.get("lat"), r.get("lon")
        if lat is None or lon is None or not (w <= lon <= e and s <= lat <= n_):
            continue
        col = min(cols - 1, int((lon - w) / (e - w) * cols))
        row = min(rows - 1, int((n_ - lat) / (n_ - s) * rows))
        v = grid[row * cols + col]
        if v:
            r["row"] = int(v) - 1
            hit += 1
    first = sum(1 for r in listings if r.get("row") == 0)
    print(f"  parcel rows: {cols}×{rows} grid, {hit}/{len(listings)} listings on a "
          f"mapped parcel ({first} in first row to the water)")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(here, "..", "data")
    os.makedirs(data_dir, exist_ok=True)
    meta_path = os.path.join(data_dir, "meta.json")

    # Rail geometry and the S-tog stop list come first: the per-listing
    # nearest-station distances below are computed against ALL_STATIONS.
    print("Fetching S-train track geometry (issue #6)…")
    prev_rail = None
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                prev_rail = json.load(f).get("railGeom")
        except Exception:
            pass
    rail_geom = fetch_rail_geometry() or prev_rail
    ALL_STATIONS.extend(fetch_stog_stations(rail_geom))

    out = []
    counts = {"condo": 0, "villa": 0}
    atypes = {}   # (queried type, raw addressType) -> n, to verify the mapping
    for muni, (name, _code) in MUNICIPALITIES.items():
        for t in TYPES:
            got = 0
            for case in fetch(muni, t):
                # count every raw type we see (including the ones we drop) so the
                # vocabulary log stays honest about what boligsiden returns
                k = (t, str(case.get("addressType")))
                atypes[k] = atypes.get(k, 0) + 1
                rec = trim(case, t)
                if rec is None:
                    continue
                out.append(rec)
                counts[rec["t"]] += 1
                got += 1
            print(f"  {name:16} {t:6} {got}")
    print("  addressType vocabulary (queried → raw):")
    for (qt, at), n in sorted(atypes.items(), key=lambda kv: -kv[1]):
        mark = ("  [dropped — not an ordinary home]"
                if str(at).strip().lower() in ADDR_TYPE_SKIP else "")
        print(f"    {qt:6} → {at:24} {n}{mark}")
    uniq = {r["id"]: r for r in out if r["id"]}
    listings = list(uniq.values())

    print("Fetching metro / letbane overlay…")
    prev_transit = None
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                prev_transit = json.load(f).get("transit")
        except Exception:
            pass
    transit = merge_transit(fetch_transit(), prev_transit)
    annotate_metro(listings, transit)

    print("Fetching amenities — supermarkets/schools/daycare (issue #7)…")
    pois = fetch_pois()
    poi_path = os.path.join(data_dir, "poi.json")
    if not pois and os.path.exists(poi_path):
        try:
            with open(poi_path, encoding="utf-8") as f:
                pois = json.load(f).get("items")
        except Exception:
            pass
    if pois:
        with open(poi_path, "w", encoding="utf-8") as f:
            json.dump({"generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "count": len(pois), "items": pois}, f, ensure_ascii=False, separators=(",", ":"))
    annotate_poi_distance(listings, pois)

    print("Fetching location features — motorway / water / nature (issue #8)…")
    annotate_geo_distance(listings, fetch_geo_features())

    # Road-traffic noise: a static layer fetched once by scripts/fetch_noise_once.py
    # (EU noise mapping is redone every ~5 years), so just read it off disk.
    noise_path = os.path.join(data_dir, "noise.json")
    if os.path.exists(noise_path):
        print("Matching addresses against the 2022 road-noise map…")
        try:
            with open(noise_path, encoding="utf-8") as f:
                annotate_noise(listings, json.load(f))
        except Exception as ex:
            print(f"  noise annotation failed ({ex})", file=sys.stderr)

    # Coastal matrikel rows: another static artefact, built once by
    # scripts/fetch_parcels_once.py, since parcel boundaries barely move.
    row_path = os.path.join(data_dir, "parcelrow.json")
    if os.path.exists(row_path):
        print("Matching addresses against the coastal matrikel rows…")
        try:
            with open(row_path, encoding="utf-8") as f:
                annotate_parcel_row(listings, json.load(f))
        except Exception as ex:
            print(f"  parcel row annotation failed ({ex})", file=sys.stderr)

    print("Confirming hjemfald/tilbagekøb on cheap outliers…")
    confirm_encumbrance(listings)

    # Grundareal: another static, one-off artefact (fetch_parcel_areas_once.py).
    # Boliga's sold records carry a lot size on essentially none of them, so
    # this is what actually lets the comps' lot-size matching do anything.
    grundareal = None
    ga_path = os.path.join(data_dir, "grundareal.json")
    if os.path.exists(ga_path):
        try:
            with open(ga_path, encoding="utf-8") as f:
                grundareal = json.load(f)
        except Exception as ex:
            print(f"  grundareal load failed ({ex})", file=sys.stderr)

    print("Fetching realised sold prices (Boliga / tinglysning)…")
    sold = fetch_sold(listings)
    if sold:
        n_comp = annotate_comps(listings, sold.pop("points", []),
                                sold.get("seriesAll"), sold.get("series"), grundareal)
        print(f"  comps: {n_comp}/{len(listings)} listings got a local realised benchmark")

    with open(os.path.join(data_dir, "listings.json"), "w", encoding="utf-8") as f:
        json.dump(listings, f, ensure_ascii=False, separators=(",", ":"))

    print("Fetching municipality boundaries…")
    geo = fetch_boundaries()
    with open(os.path.join(data_dir, "geo.json"), "w", encoding="utf-8") as f:
        json.dump(geo, f, ensure_ascii=False, separators=(",", ":"))

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ndates = merge_history(data_dir, snapshot(listings, today), today)
    track = track_listings(data_dir, listings, today)

    print("Fetching Danmarks Statistik price index (EJ56)…")
    dst = fetch_dst_index()
    if dst:
        with open(os.path.join(data_dir, "priceindex.json"), "w", encoding="utf-8") as f:
            json.dump(dst, f, ensure_ascii=False, separators=(",", ":"))

    print("Fetching Boligøkonomisk Videncenter long real index…")
    bvc = fetch_bvc()
    if bvc:
        with open(os.path.join(data_dir, "bvc.json"), "w", encoding="utf-8") as f:
            json.dump(bvc, f, ensure_ascii=False, separators=(",", ":"))

    if sold:
        with open(os.path.join(data_dir, "sold.json"), "w", encoding="utf-8") as f:
            json.dump(sold, f, ensure_ascii=False, separators=(",", ":"))

    print("Fetching realkreditrenter (Nationalbanken)…")
    mortgage = fetch_mortgage()
    if mortgage:
        with open(os.path.join(data_dir, "mortgage.json"), "w", encoding="utf-8") as f:
            json.dump(mortgage, f, ensure_ascii=False, separators=(",", ":"))

    meta = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "boligsiden.dk",
        "total": len(listings),
        "counts": {"condo": counts["condo"], "villa": counts["villa"]},
        "strainNearM": STRAIN_NEAR_M,
        "historyDays": ndates,
        "municipalities": [{"slug": s, "name": v[0], "hasGeo": s in geo}
                           for s, v in MUNICIPALITIES.items()],
        "stations": [
            {"name": n, "corridor": c, "lat": la, "lon": lo, "strain": st}
            for (n, c, la, lo, st) in ALL_STATIONS
        ],
        "lines": [{"corridor": c, "label": LINE_LABELS[c], "stops": stops}
                  for c, stops in LINES.items()],
        "transit": transit,   # metro + letbane overlay (None if the fetch failed)
        "railGeom": rail_geom,   # real S-train track geometry per ref (issue #6)
        "hasSold": bool(sold),   # realised sold-price data available this build
        "hasMortgage": bool(mortgage),   # realkreditrenter available this build
    }
    with open(os.path.join(data_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, separators=(",", ":"))

    n_hf = sum(1 for r in listings if r.get("hf"))
    n_sold = sum(len(v) for v in (sold or {}).get("byMuni", {}).values()) if sold else 0
    print(f"\nWrote {len(listings)} listings (condo={counts['condo']}, "
          f"villa={counts['villa']}), {len(geo)} boundaries, {ndates} history date(s), "
          f"{track['tracked']} tracked ({track['live']} live, {track['withChanges']} with price changes), "
          f"{n_hf} with hjemfald/tilbagekøb, "
          f"{'sold data for ' + str(n_sold) + ' kommune×type groups' if sold else 'no sold data'}.")


if __name__ == "__main__":
    main()
