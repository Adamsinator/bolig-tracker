#!/usr/bin/env python3
"""One-off: work out how many matrikler sit between a coastal home and the water,
and bake the answer into a grid.

Distance to the shore cannot tell a waterfront garden from the house behind it —
two homes 60 m from the water, one with the sea at the end of the lawn and one
looking at a neighbour's roof, are the same number. That is where the fair-value
model is weakest: median out-of-sample error is 13.6 % within 300 m of water
against 11.4 % beyond it.

A matrikel that shares a boundary with the coastline is first row *by
definition*, which is a fact rather than a proxy. So: pull the cadastral parcels
along the coast, mark the ones touching the water as row 0, walk outward by
adjacency, and rasterise the result onto the same grid geometry as
data/noise.json so the daily build reads it with an array index.

Parcel boundaries barely move — an existing coastal plot is subdivided maybe
once a decade — so this runs once and the artefact is committed, exactly like the
noise grid. Source is DAWA today; it is switched off 1 October 2026, and
Datafordeler's MAT/v2 is the successor (see #27). Only the fetch below changes
when that happens; everything after it is source-agnostic.
"""
import base64
import gzip
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
OUT = os.path.join(DATA, "parcelrow.json")

DAWA = "https://api.dataforsyningen.dk"
UA = {"User-Agent": "bolig-tracker/1.0 (+https://boligtracker.dk)",
      "Accept": "application/json"}

# same corridor and grid geometry as data/noise.json, so the reader is identical
# — Region Hovedstaden minus Bornholm (#24), matches build_data.py's CORRIDOR_BBOX
W, S, E, N = 11.85, 55.55, 12.70, 56.15
RES_M = 10.0
BAND_M = 600.0          # only parcels this close to the shore matter
STEP_M = 250.0          # spacing of the sample circles along the coastline
RADIUS_M = 400          # DAWA circle radius per sample
TOUCH_M = 15.0          # OSM coastline vs matriklen won't align to the metre
ADJ_M = 3.0             # two parcels sharing a boundary, allowing for rounding
MAX_ROW = 4             # beyond this the distinction stops meaning anything

OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


def _m_per_deg(lat):
    return 110540.0, 111320.0 * math.cos(math.radians(lat))


MY, MX = _m_per_deg((S + N) / 2)


def dist_m(a, b):
    return math.hypot((a[1] - b[1]) * MY, (a[0] - b[0]) * MX)


# ---------------------------------------------------------------- coastline
def fetch_coastline():
    q = (f'[out:json][timeout:120];(way["natural"="coastline"]({S},{W},{N},{E}););out geom;')
    for url in OVERPASS:
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": q}).encode(),
                                         headers={"User-Agent": UA["User-Agent"]})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.load(r)
            ways = [[(p["lon"], p["lat"]) for p in (el.get("geometry") or [])]
                    for el in data.get("elements", [])]
            ways = [w for w in ways if len(w) > 1]
            if ways:
                print(f"  coastline: {len(ways)} ways, "
                      f"{sum(len(w) for w in ways)} nodes  (via {urllib.parse.urlparse(url).netloc})")
                return ways
        except Exception as ex:
            print(f"  overpass {urllib.parse.urlparse(url).netloc} failed ({ex})", file=sys.stderr)
    sys.exit("could not fetch the coastline")


def walk(ways, step_m):
    """Sample points along the coastline at a fixed spacing."""
    pts, carry = [], 0.0
    for w in ways:
        for a, b in zip(w, w[1:]):
            d = dist_m(a, b)
            if d <= 0:
                continue
            t = carry
            while t < d:
                f = t / d
                pts.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
                t += step_m
            carry = t - d
    return pts


# ---------------------------------------------------------------- parcels
def get_json(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as ex:
            if ex.code in (400, 404):
                return None
            time.sleep(1.5 * (i + 1))
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def fetch_parcels(samples):
    """Cadastral parcels within RADIUS_M of each sample point, deduped."""
    seen, out = set(), []
    for i, (lon, lat) in enumerate(samples):
        url = (f"{DAWA}/jordstykker?cirkel={lon:.6f},{lat:.6f},{RADIUS_M}"
               f"&format=geojson&srid=4326")
        fc = get_json(url)
        feats = (fc or {}).get("features") or []
        for f in feats:
            p = f.get("properties") or {}
            key = (p.get("ejerlavkode"), p.get("matrikelnr"))
            if key in seen or key == (None, None):
                continue
            g = f.get("geometry") or {}
            rings = []
            if g.get("type") == "Polygon":
                rings = [g["coordinates"]]
            elif g.get("type") == "MultiPolygon":
                rings = g["coordinates"]
            if not rings:
                continue
            seen.add(key)
            out.append({"key": key, "rings": rings})
        if (i + 1) % 25 == 0:
            print(f"    {i+1}/{len(samples)} samples · {len(out)} parcels", flush=True)
    return out


# ---------------------------------------------------------------- geometry
DENSIFY_M = 4.0


def outer_points(par):
    """Outer-ring outline densified to ~DENSIFY_M spacing.

    Thinning the vertices instead loses corners, and comparing bare vertices
    misses the common case where two parcels share a long boundary without
    sharing a single point — one long plot beside three short ones touches all
    three with no coincident corner anywhere. Densifying turns both the
    coast-touch and adjacency tests into plain point-distance checks that
    actually hold."""
    pts = []
    for poly in par["rings"]:
        if not poly or not poly[0]:
            continue
        ring = poly[0]
        for a, b in zip(ring, list(ring[1:]) + [ring[0]]):
            pts.append((a[0], a[1]))
            d = dist_m(a, b)
            if d > DENSIFY_M:
                for k in range(1, int(d / DENSIFY_M)):
                    f = k * DENSIFY_M / d
                    pts.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    return pts


def bbox_of(par):
    xs = [p[0] for p in outer_points(par)]
    ys = [p[1] for p in outer_points(par)]
    return min(xs), min(ys), max(xs), max(ys)


def main():
    print("1) coastline…", flush=True)
    ways = fetch_coastline()
    samples = walk(ways, STEP_M)
    print(f"   {len(samples)} sample points at {STEP_M:.0f} m spacing")

    print("\n2) parcels along the shore (DAWA)…", flush=True)
    parcels = fetch_parcels(samples)
    print(f"   {len(parcels)} distinct parcels")
    if not parcels:
        sys.exit("no parcels came back — cannot build the grid")

    for p in parcels:
        p["pts"] = outer_points(p)
        p["bbox"] = bbox_of(p)

    print("\n3) row 0: parcels whose boundary meets the coastline…", flush=True)
    CELL = 0.004
    cgrid = {}
    for w in ways:
        for x, y in w:
            cgrid.setdefault((int(y / CELL), int(x / CELL)), []).append((x, y))

    def near_coast(pt):
        i, j = int(pt[1] / CELL), int(pt[0] / CELL)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for c in cgrid.get((i + di, j + dj), ()):
                    if dist_m(pt, c) <= TOUCH_M:
                        return True
        return False

    row = {}
    for p in parcels:
        if any(near_coast(pt) for pt in p["pts"]):
            row[p["key"]] = 0
    print(f"   {len(row)} parcels touch the water")

    print("\n4) walking outward by adjacency…", flush=True)
    # Spatial hash of every boundary point at roughly the adjacency tolerance, so
    # a neighbour lookup is a handful of bucket reads rather than a scan over all
    # parcels — the difference between seconds and hours at real scale.
    ax, ay = ADJ_M / MX, ADJ_M / MY
    pgrid = {}
    for idx, p in enumerate(parcels):
        for x, y in p["pts"]:
            pgrid.setdefault((int(y / ay), int(x / ax)), []).append((idx, x, y))

    for r in range(MAX_ROW):
        frontier = [p for p in parcels if row.get(p["key"]) == r]
        added = 0
        for p in frontier:
            for x, y in p["pts"]:
                i, j = int(y / ay), int(x / ax)
                for di in (-1, 0, 1):
                    for dj in (-1, 0, 1):
                        for idx, qx, qy in pgrid.get((i + di, j + dj), ()):
                            q = parcels[idx]
                            if q["key"] in row:
                                continue
                            # the bucket only narrows the field — a neighbouring
                            # cell reaches up to 3x the tolerance, so the actual
                            # distance still has to be checked
                            if dist_m((x, y), (qx, qy)) <= ADJ_M:
                                row[q["key"]] = r + 1
                                added += 1
        print(f"   row {r+1}: +{added}", flush=True)
        if not added:
            break
    print(f"   {len(row)} parcels have a row index")

    print("\n5) rasterising onto the noise grid geometry…", flush=True)
    clat = math.cos(math.radians((S + N) / 2))
    cols = int(round((E - W) * 111320.0 * clat / RES_M))
    rows_ = int(round((N - S) * 110540.0 / RES_M))
    grid = bytearray(cols * rows_)          # 0 = unknown, else row+1

    def rasterise(par, val):
        x0, y0, x1, y1 = par["bbox"]
        c0 = max(0, int((x0 - W) / (E - W) * cols))
        c1 = min(cols - 1, int((x1 - W) / (E - W) * cols))
        r0 = max(0, int((N - y1) / (N - S) * rows_))
        r1 = min(rows_ - 1, int((N - y0) / (N - S) * rows_))
        for poly in par["rings"]:
            ring = poly[0]
            for rr in range(r0, r1 + 1):
                lat = N - (rr + 0.5) * (N - S) / rows_
                xs = []
                for a, b in zip(ring, ring[1:] + ring[:1]):
                    if (a[1] > lat) != (b[1] > lat):
                        xs.append(a[0] + (lat - a[1]) * (b[0] - a[0]) / (b[1] - a[1]))
                xs.sort()
                for k in range(0, len(xs) - 1, 2):
                    ca = max(c0, int((xs[k] - W) / (E - W) * cols))
                    cb = min(c1, int((xs[k + 1] - W) / (E - W) * cols))
                    for cc in range(ca, cb + 1):
                        idx = rr * cols + cc
                        if grid[idx] == 0 or val < grid[idx]:
                            grid[idx] = val

    for p in parcels:
        r = row.get(p["key"])
        if r is not None:
            rasterise(p, r + 1)

    hist = {}
    for b in grid:
        if b:
            hist[b] = hist.get(b, 0) + 1
    print(f"   cells per row: { {k-1: v for k, v in sorted(hist.items())} }")
    if not hist:
        sys.exit("nothing rasterised")

    out = {
        "source": "Matriklen via DAWA jordstykker; coastline © OpenStreetMap (ODbL)",
        "note": "value = row index + 1; 0 means no parcel mapped here. "
                "row 0 = matrikel bordering the coastline.",
        "bbox": [W, S, E, N], "cols": cols, "rows": rows_, "resM": RES_M,
        "touchM": TOUCH_M, "adjM": ADJ_M, "parcels": len(parcels),
        "enc": "gzip+base64",
        "data": base64.b64encode(gzip.compress(bytes(grid), 9)).decode("ascii"),
    }
    os.makedirs(DATA, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    mb = os.path.getsize(OUT) / 1e6
    print(f"\n6) wrote data/parcelrow.json — {cols}×{rows_}, {mb:.2f} MB")
    if mb > 45:
        sys.exit("too large to commit")


if __name__ == "__main__":
    main()
