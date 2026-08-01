#!/usr/bin/env python3
"""One-off: pull every matrikel's registered area (registreretareal) across
Region Hovedstaden and write data/grundareal.json.

Why this matters: comps (annotate_comps in build_data.py) try to narrow the
sold-price sample to houses of a similar lot size, but Boliga's sold records
carry a lot size on 0 of 12,973 records — the matching code has been dead since
the day it shipped. Every parcel does carry its area on the register, though,
so a one-off area lookup keyed by location fixes it for good.

Matrikelgrænser barely move — an existing plot is subdivided maybe once a
decade — so this runs once, like fetch_noise_once.py and fetch_parcels_once.py.
Source is DAWA's jordstykker endpoint, live until 1 October 2026; Datafordeler's
MAT/v2 register carries the same registreretareal field (see #27) for whenever
this needs to be re-run after DAWA is retired.

Storage is a flat quantized (lat, lon, area) array, not a raster grid: unlike
first-row detection (#25), this needs no polygon adjacency, just "which parcel
is nearest" — the same coarse spatial hash already used by annotate_comps and
annotate_geo_distance in build_data.py. Simpler code, no geometry to fetch.
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

DAWA = "https://api.dataforsyningen.dk"
UA = {"User-Agent": "bolig-tracker/1.0 (+https://boligtracker.dk)", "Accept": "application/json"}
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "data", "grundareal.json")
PER_SIDE = 500

# Region Hovedstaden, Bornholm excluded (see issue #24) — the 14 kommuner the
# daily build already covers, plus the 14 more that would be needed once that
# expansion happens. Fetching all 28 now costs about the same as fetching 14
# (~17 min either way at this rate) and means this never needs doing twice.
KOMMUNER = {
    "0101": "København", "0147": "Frederiksberg", "0157": "Gentofte",
    "0173": "Lyngby-Taarbæk", "0230": "Rudersdal", "0159": "Gladsaxe",
    "0190": "Furesø", "0201": "Allerød", "0219": "Hillerød", "0223": "Hørsholm",
    "0151": "Ballerup", "0163": "Herlev", "0240": "Egedal", "0210": "Fredensborg",
    "0165": "Albertslund", "0153": "Brøndby", "0155": "Dragør",
    "0250": "Frederikssund", "0161": "Glostrup", "0270": "Gribskov",
    "0260": "Halsnæs", "0217": "Helsingør", "0167": "Hvidovre",
    "0169": "Høje-Taastrup", "0183": "Ishøj", "0175": "Rødovre",
    "0185": "Tårnby", "0187": "Vallensbæk",
}

# quantization: area stored as round(log2(area) * SCALE) in one byte-ish int,
# reconstructed as 2**(v/SCALE) on read. Comps match on +/-40% tolerance, so
# even a coarse step here is far more precise than the tolerance needs.
LOG_SCALE = 20.0


def fetch(url, tries=5):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as ex:
            if ex.code in (400, 404):
                return None
            time.sleep(2 * (i + 1))
        except Exception:
            time.sleep(2 * (i + 1))
    return None


def main():
    flat = []          # [lat1, lon1, logarea1, lat2, lon2, logarea2, ...]
    t0 = time.time()
    for code, name in KOMMUNER.items():
        n_before = len(flat) // 3
        side = 1
        while True:
            url = (f"{DAWA}/jordstykker?kommunekode={code}"
                   f"&per_side={PER_SIDE}&side={side}")
            recs = fetch(url)
            if recs is None:
                print(f"  {name} ({code}): stopped at side {side} (request failed)",
                      file=sys.stderr)
                break
            for rec in recs:
                vc = rec.get("visueltcenter") or {}
                lon, lat = vc.get("x"), vc.get("y")
                area = rec.get("registreretareal")
                if lon is None or lat is None or not area or area <= 0:
                    continue
                logv = round(math.log2(area) * LOG_SCALE) if area else 0
                flat.extend((round(lat, 5), round(lon, 5), logv))
            if len(recs) < PER_SIDE or side > 200:
                break
            side += 1
        got = len(flat) // 3 - n_before
        # every one of these kommuner is populated, so zero back from a whole
        # run is almost certainly a wrong kommunekode rather than an empty area
        flag = "  <-- SUSPICIOUS: check this kommunekode" if got == 0 else ""
        print(f"  {name} ({code}): {got} parcels{flag}", flush=True)
    elapsed = time.time() - t0
    n = len(flat) // 3
    print(f"\nfetched {n} parcels across {len(KOMMUNER)} kommuner in {elapsed/60:.1f} min")
    if n == 0:
        sys.exit("nothing fetched — refusing to write an empty file")

    blob = base64.b64encode(gzip.compress(json.dumps(flat, separators=(",", ":")).encode(), 9)).decode()
    out = {
        "source": "Matriklen via DAWA jordstykker (registreretareal)",
        "note": "flat [lat,lon,logarea]*N array; area = round(2**(logarea/20)). "
                "Nearest-point lookup, not exact-parcel: fine for +/-40% comp matching.",
        "logScale": LOG_SCALE, "count": n, "kommuner": len(KOMMUNER),
        "enc": "gzip+base64", "data": blob,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    mb = os.path.getsize(OUT) / 1e6
    print(f"wrote data/grundareal.json — {n} parcels, {mb:.2f} MB")
    if mb > 45:
        sys.exit("too large to commit")


if __name__ == "__main__":
    import math
    main()
