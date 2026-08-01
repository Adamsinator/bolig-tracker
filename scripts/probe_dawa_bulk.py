#!/usr/bin/env python3
"""Temporary probe #2: total parcel count and the registreretareal field.

Probe #1 confirmed kommunekode + page/per_side pagination works — page 1 and
page 2 return distinct matrikelnr. It crashed counting Hørsholm because the
response-size cap (400KB) was too small for geojson's verbose properties at
per_side=500, truncating mid-record. Raise the cap and drop the geometry (we
only need the count + the area field name here, not the shapes).
"""
import json
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "bolig-tracker/1.0 (+https://boligtracker.dk)", "Accept": "application/json"}
DAWA = "https://api.dataforsyningen.dk"
K = "0223"  # Hørsholm


def get(url, cap=6_000_000):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
            return r.status, r.read(cap)
    except urllib.error.HTTPError as ex:
        try:
            return ex.code, ex.read(400)
        except Exception:
            return ex.code, b""
    except Exception as ex:
        return None, f"{type(ex).__name__}: {str(ex)[:80]}".encode()


print("=== full property set on one parcel (plain, no geometry) ===")
code, body = get(f"{DAWA}/jordstykker?kommunekode={K}&per_side=1&side=1")
print(f"  HTTP {code}")
if code == 200:
    recs = json.loads(body.decode("utf-8", "replace"))
    if recs:
        print("  ALL keys:", sorted(recs[0].keys()))
        area_keys = [k for k in recs[0] if "areal" in k.lower() or "area" in k.lower()]
        print("  area-ish keys:", area_keys)
        for k in area_keys:
            print(f"    {k} = {recs[0][k]!r}")
else:
    print(" ", body[:400].decode("utf-8", "replace"))

print("\n=== total parcel count for Hørsholm (0223), plain format, no geometry ===")
total, side, per_side = 0, 1, 500
t0 = time.time()
while True:
    code, body = get(f"{DAWA}/jordstykker?kommunekode={K}&per_side={per_side}&side={side}")
    if code != 200:
        print(f"  stopped at side {side}: HTTP {code}, {body[:150]!r}")
        break
    try:
        recs = json.loads(body.decode("utf-8", "replace"))
    except Exception as ex:
        print(f"  JSON error at side {side}, {len(body)} bytes: {ex}")
        break
    total += len(recs)
    if len(recs) < per_side or side > 60:
        break
    side += 1
elapsed = time.time() - t0
print(f"  {total} parcels across {side} page(s) of {per_side}, {elapsed:.1f}s "
      f"({elapsed/max(side,1):.2f}s/page)")

# Hørsholm is roughly 1/28th of Region Hovedstaden's land area by rough eye —
# extrapolate honestly rather than assume linearity, this is just a ballpark.
print(f"\n  rough scale check: at this rate, ~28 kommuner would be "
      f"~{total*28:,} parcels and ~{elapsed*28/60:.0f} min of pure fetch time "
      f"(no parallelism, no rate-limit backoff)")

print("\nDelete this script and its workflow once read.")
