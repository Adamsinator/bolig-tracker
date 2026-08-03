#!/usr/bin/env python3
"""Diagnostic-only: sanity-check a hand-rolled UTM32N (EPSG:25832) -> WGS84
inverse projection against the boundary fetch_boundaries() already produces
today via DAWA (which returns WGS84 directly). Same kommune (Hørsholm,
0223), two independent sources — if the converted DAGI/WKT bbox lands close
to the DAWA bbox, the projection math is trustworthy enough to cut over.
Not wired into build_data.py yet; this is purely a before-you-trust-it check.
"""
import json
import math
import os
import re
import urllib.request

API_KEY = os.environ["DATAFORDELER_API"]
DAWA = "https://api.dataforsyningen.dk"
UA = {"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"}


def utm32n_to_wgs84(easting, northing):
    """Inverse Transverse Mercator, ETRS89/UTM zone 32N -> geographic lat/lon
    in degrees. Standard Snyder (1987) footprint-latitude algorithm. GRS80
    ellipsoid (ETRS89); WGS84 ellipsoid differs by <0.1mm in these terms, so
    treating ETRS89 output as WGS84 is standard practice at this precision."""
    a = 6378137.0
    f = 1 / 298.257222101
    k0 = 0.9996
    E0 = 500000.0
    lon0 = math.radians(9.0)  # zone 32 central meridian

    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)

    M = northing / k0
    mu = M / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))

    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu
            + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))

    C1 = ep2 * math.cos(phi1) ** 2
    T1 = math.tan(phi1) ** 2
    N1 = a / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    R1 = a * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    D = (easting - E0) / (N1 * k0)

    lat = phi1 - (N1 * math.tan(phi1) / R1) * (
        D ** 2 / 2
        - (5 + 3 * T1 + 10 * C1 - 4 * C1 ** 2 - 9 * ep2) * D ** 4 / 24
        + (61 + 90 * T1 + 298 * C1 + 45 * T1 ** 2 - 252 * ep2 - 3 * C1 ** 2) * D ** 6 / 720
    )
    lon = lon0 + (
        D - (1 + 2 * T1 + C1) * D ** 3 / 6
        + (5 - 2 * C1 + 28 * T1 - 3 * C1 ** 2 + 8 * ep2 + 24 * T1 ** 2) * D ** 5 / 120
    ) / math.cos(phi1)

    return math.degrees(lat), math.degrees(lon)


def dawa_bbox(code):
    url = f"{DAWA}/kommuner/{code}?format=geojson&srid=4326"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        feat = json.load(r)
    g = feat.get("geometry") or {}
    polys = g.get("coordinates") or []
    if g.get("type") == "Polygon":
        polys = [polys]
    xs, ys = [], []
    for poly in polys:
        for x, y in (poly[0] if poly else []):
            xs.append(x); ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def dagi_wkt(code):
    base = "https://graphql.datafordeler.dk/DAGI/v2"
    now = "2026-08-02T12:00:00Z"
    q = f'''
    query {{
      DAGI_Kommuneinddeling(
        first: 1
        registreringstid: "{now}"
        virkningstid: "{now}"
        where: {{ kommunekode: {{ eq: "{code}" }} }}
      ) {{
        nodes {{ geometri {{ wkt }} }}
      }}
    }}
    '''
    body = json.dumps({"query": q}).encode("utf-8")
    req = urllib.request.Request(f"{base}?apiKey={API_KEY}", data=body,
                                  headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    return data["data"]["DAGI_Kommuneinddeling"]["nodes"][0]["geometri"]["wkt"]


def parse_wkt_multipolygon(wkt):
    """Yield (x, y) UTM32N pairs from a MULTIPOLYGON WKT string — enough to
    get a bbox, not a full ring-structure parse."""
    nums = re.findall(r'(-?\d+\.?\d*) (-?\d+\.?\d*)', wkt)
    return [(float(x), float(y)) for x, y in nums]


def main():
    code = "0223"  # Hørsholm
    print(f"1) DAWA bbox (WGS84, known-good) for kommune {code}...", flush=True)
    dw = dawa_bbox(code)
    print(f"   DAWA: lon {dw[0]:.5f}..{dw[2]:.5f}, lat {dw[1]:.5f}..{dw[3]:.5f}")

    print(f"\n2) DAGI WKT (UTM32N) for kommune {code}, converting...", flush=True)
    wkt = dagi_wkt(code)
    pts_utm = parse_wkt_multipolygon(wkt)
    print(f"   {len(pts_utm)} vertices parsed from WKT")
    pts_wgs = [utm32n_to_wgs84(e, n) for e, n in pts_utm]
    lats = [p[0] for p in pts_wgs]
    lons = [p[1] for p in pts_wgs]
    print(f"   DAGI->WGS84: lon {min(lons):.5f}..{max(lons):.5f}, lat {min(lats):.5f}..{max(lats):.5f}")

    print("\n3) agreement check:")
    print(f"   lon range diff: {abs(min(lons)-dw[0]):.6f} / {abs(max(lons)-dw[2]):.6f} deg")
    print(f"   lat range diff: {abs(min(lats)-dw[1]):.6f} / {abs(max(lats)-dw[3]):.6f} deg")
    print("   (should be well under 0.001 deg == ~100m if the projection math is right;"
          " some real difference is expected since DAWA/DAGI may generalise coastline slightly differently)")


if __name__ == "__main__":
    main()
