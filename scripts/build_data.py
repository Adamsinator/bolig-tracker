#!/usr/bin/env python3
"""Pull housing listings for the S-train corridor from boligsiden.dk's public API
and write two compact files consumed by the static site:

    data/listings.json  – one trimmed record per listing (~0.3 KB each)
    data/meta.json       – generated-at, counts, municipality names, stations

No API key, no auth. Dependency-free (stdlib only) so it runs locally and in CI.
"""
import contextlib
import json
import math
import os
import re
import signal
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
}
MUNI_NAME = {s: v[0] for s, v in MUNICIPALITIES.items()}
TYPES = ["condo", "villa"]  # ejerlejlighed, villa

# "Near the S-train" heuristic (metres, straight-line to nearest S-train station).
STRAIN_NEAR_M = 1200


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
            headers={"Accept": "application/json", "User-Agent": "bolig-tracker/1.0"},
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


def nearest_station(lat, lon):
    """Return (name, corridor, dist_m, is_strain) for the closest rail station."""
    best = None
    for name, corridor, slat, slon, strain in STATIONS:
        d = haversine_m(lat, lon, slat, slon)
        if best is None or d < best[2]:
            best = (name, corridor, d, strain)
    return best


def pick_thumb(images):
    """Smallest image wider than ~250px, else the first source."""
    if not images:
        return None
    srcs = images[0].get("imageSources") or []
    if not srcs:
        return None
    wide = [s for s in srcs if (s.get("size") or {}).get("width", 0) >= 250]
    chosen = min(wide, key=lambda s: s["size"]["width"]) if wide else srcs[0]
    return chosen.get("url")


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


def trim(case):
    addr = case.get("address") or {}
    coords = case.get("coordinates") or {}
    lat, lon = coords.get("lat"), coords.get("lon")
    if lat is None or lon is None:
        return None
    st_name, st_corr, st_d, st_is = nearest_station(lat, lon)
    # nearest S-train specifically (may differ from overall nearest)
    strain_only = min(
        (s for s in STATIONS if s[4]),
        key=lambda s: haversine_m(lat, lon, s[2], s[3]),
    )
    strain_d = haversine_m(lat, lon, strain_only[2], strain_only[3])
    return {
        "id": case.get("caseID"),
        "t": "villa" if case.get("addressType") == "villa" else "condo",
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
        "img": pick_thumb(case.get("images")),
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

def _dump_dims(tid):
    info = _dst_get(f"tableinfo/{tid}?format=JSON")
    if not info:
        return
    print(f"  mortgage {tid}: {info.get('text')}")
    for v in info.get("variables") or []:
        vals = v.get("values") or []
        vid = v.get("id")
        if len(vals) <= 90:
            print(f"    var {vid} ({v.get('text')}) [{len(vals)}]: "
                  + "; ".join(f"{x.get('id')}={x.get('text')}" for x in vals))
        else:
            print(f"    var {vid} ({v.get('text')}) [{len(vals)}]: {vals[0].get('id')}..{vals[-1].get('id')}")

def fetch_mortgage():
    # The classic rate tables discontinued their mortgage-bond series (~2012-14).
    # The live effective mortgage rate by fixation lives in the MFI new/outstanding
    # lending tables. Broaden the table listing to any "rente" table and probe the
    # MFI realkredit tables' dimensions to locate the effective-rate measure.
    tables = _dst_get("tables?format=JSON")
    if tables:
        hits = [t for t in tables if "rente" in str(t.get("text", "")).lower()
                and any(k in str(t.get("text", "")).lower() for k in
                        ("realkredit", "udlån", "udlaan", "husholdning", "mfi", "penge"))]
        print(f"  mortgage: {len(hits)} lending-rate candidate tables")
        for t in hits[:40]:
            print(f"    {t.get('id')}: {t.get('text')}  [{t.get('updated', '')}]")
    for tid in ("DNRNURI", "DNRUURI", "DNMUDL"):
        _dump_dims(tid)
    return None   # discovery only — no data emitted yet


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
        req = urllib.request.Request(BVC_URL, headers={"User-Agent": "bolig-tracker/1.0"})
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
OVERPASS_MIRRORS = ["https://overpass.kumi.systems/api/interpreter",
                    "https://overpass-api.de/api/interpreter"]
TRANSIT_BBOX = (55.55, 12.34, 55.86, 12.70)   # s, w, n, e — greater Copenhagen

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
    for url in OVERPASS_MIRRORS:
        try:
            req = urllib.request.Request(url, data=query.encode("utf-8"),
                                         headers={"User-Agent": "bolig-tracker/1.0"})
            with hard_timeout(45), urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except Exception as ex:
            print(f"  transit fetch via {url} failed ({ex})", file=sys.stderr)
    return None

def fetch_transit():
    s, w, n, e = TRANSIT_BBOX
    # Two independent queries. Stations power the near-metro distance and are
    # cheap, so we fetch them separately from the heavier line geometry — a
    # timeout on one no longer wipes out the other.
    stations, seen = [], set()
    sdata = _overpass(
        f'[out:json][timeout:30];'
        f'(node["station"="subway"]({s},{w},{n},{e});'
        f'node["station"="light_rail"]({s},{w},{n},{e}););out body;')
    for el in (sdata or {}).get("elements", []):
        tags = el.get("tags") or {}
        if el.get("type") == "node" and tags.get("name"):
            key = (round(el["lat"], 4), round(el["lon"], 4))
            if key not in seen:
                seen.add(key)
                mode = "letbane" if tags.get("station") == "light_rail" else "metro"
                stations.append({"name": tags["name"], "mode": mode,
                                 "lat": round(el["lat"], 5), "lon": round(el["lon"], 5)})
    print(f"  transit: {len(stations)} stations fetched")

    lines = []
    ldata = _overpass(
        f'[out:json][timeout:30];'
        f'(way["railway"="subway"]({s},{w},{n},{e});'
        f'way["railway"="light_rail"]({s},{w},{n},{e}););out geom;')
    for el in (ldata or {}).get("elements", []):
        tags = el.get("tags") or {}
        if el.get("type") == "way" and tags.get("railway") in ("subway", "light_rail") and el.get("geometry"):
            mode = "metro" if tags["railway"] == "subway" else "letbane"
            seg = [[round(p["lat"], 5), round(p["lon"], 5)] for p in el["geometry"]]
            if len(seg) > 1:
                lines.append({"mode": mode, "ref": tags.get("ref") or "",
                              "colour": tags.get("colour") or tags.get("color"), "segs": [seg]})

    if not lines and not stations:
        return None
    print(f"  transit: {len(lines)} line segments fetched")
    return {"lines": lines, "stations": stations}


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

def _boliga_sold_page(code, ptype, date_min, page):
    qs = urllib.parse.urlencode({
        "municipality": code, "propertyType": ptype, "salesDateMin": date_min,
        "pageSize": 500, "page": page, "sort": "date-d",
    })
    for attempt in range(5):          # ~2+4+6+8s of backoff — rides out rate limits
        try:
            req = urllib.request.Request(f"{BOLIGA_SOLD}?{qs}",
                headers={"Accept": "application/json", "User-Agent": "bolig-tracker/1.0"})
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

def fetch_sold(listings):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=SOLD_MONTHS * 31)
    recent_cut = now - timedelta(days=SOLD_RECENT_DAYS)
    date_min = cutoff.strftime("%Y-%m-%d")
    by_muni, series = {}, {}
    logged_fields = False
    total = 0
    def scan_group(code, ptype):
        """Page one kommune×type; return (recent_prices, recent_m2, by_q, rows, failed)."""
        nonlocal logged_fields
        recent_prices, recent_m2, by_q, rows_seen, failed = [], [], {}, 0, False
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
                stype = str(s.get("saleType") or s.get("SaleType") or "").lower()
                if any(k in stype for k in ("fam", "auktion", "auction")):
                    continue              # arm's-length sales only
                m2 = s.get("sqmPrice") or s.get("SqmPrice")
                price = s.get("price") or s.get("Price")
                q = _quarter(sd)
                if m2 and m2 > 0:
                    if q:
                        by_q.setdefault(q, []).append(m2)
                    if d and d >= recent_cut:
                        recent_m2.append(m2)
                        if price and price > 0:
                            recent_prices.append(price)
                if d and d < cutoff:
                    stop = True
            rows_seen += len(results)
            meta_total = (data.get("meta") or {}).get("totalCount")
            if stop or (meta_total and page * 500 >= meta_total):
                break
            time.sleep(0.25)
        return recent_prices, recent_m2, by_q, rows_seen, failed

    for slug, (name, code) in MUNICIPALITIES.items():
        for t, ptype in BOLIGA_PTYPE.items():
            recent_prices, recent_m2, by_q, rows_seen, failed = scan_group(code, ptype)
            total += rows_seen
            if failed:                    # got nothing at all — one more try after a pause
                print(f"  sold {name}/{t}: page-1 fetch failed, retrying once…", file=sys.stderr)
                time.sleep(4)
                recent_prices, recent_m2, by_q, rows_seen, failed = scan_group(code, ptype)
                total += rows_seen
            if recent_m2:
                by_muni.setdefault(slug, {})[t] = {
                    "n": len(recent_m2),
                    "medPrice": round(median(recent_prices)) if recent_prices else None,
                    "medM2": round(median(recent_m2)),
                }
            if by_q:
                series.setdefault(slug, {})[t] = {q: round(median(v)) for q, v in by_q.items()}
            flag = " (FETCH FAILED)" if failed else (" (no sales in window)" if not recent_m2 else "")
            print(f"  sold {name:16} {t:6} {len(recent_m2)} recent sales{flag}")
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
    quarters = sorted({q for per in series.values() for s in per.values() for q in s})
    print(f"  sold: {total} rows scanned, {len(by_muni)} kommuner with recent sales")
    return {"generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "windowMonths": SOLD_MONTHS,
            "recentDays": SOLD_RECENT_DAYS, "byMuni": by_muni, "askingVsSold": gap,
            "quarters": quarters, "series": series}


def main():
    out = []
    counts = {"condo": 0, "villa": 0}
    for muni, (name, _code) in MUNICIPALITIES.items():
        for t in TYPES:
            got = 0
            for case in fetch(muni, t):
                rec = trim(case)
                if rec is None:
                    continue
                out.append(rec)
                counts[rec["t"]] += 1
                got += 1
            print(f"  {name:16} {t:6} {got}")
    uniq = {r["id"]: r for r in out if r["id"]}
    listings = list(uniq.values())

    here = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(here, "..", "data")
    os.makedirs(data_dir, exist_ok=True)

    print("Fetching metro / letbane overlay…")
    prev_transit = None
    meta_path = os.path.join(data_dir, "meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                prev_transit = json.load(f).get("transit")
        except Exception:
            pass
    transit = merge_transit(fetch_transit(), prev_transit)
    annotate_metro(listings, transit)

    print("Confirming hjemfald/tilbagekøb on cheap outliers…")
    confirm_encumbrance(listings)

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

    print("Fetching realised sold prices (Boliga / tinglysning)…")
    sold = fetch_sold(listings)
    if sold:
        with open(os.path.join(data_dir, "sold.json"), "w", encoding="utf-8") as f:
            json.dump(sold, f, ensure_ascii=False, separators=(",", ":"))

    print("Discovering mortgage-rate tables (Nationalbanken)…")
    fetch_mortgage()

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
            for (n, c, la, lo, st) in STATIONS
        ],
        "lines": [{"corridor": c, "label": LINE_LABELS[c], "stops": stops}
                  for c, stops in LINES.items()],
        "transit": transit,   # metro + letbane overlay (None if the fetch failed)
        "hasSold": bool(sold),   # realised sold-price data available this build
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
