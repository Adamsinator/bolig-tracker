#!/usr/bin/env python3
"""Temporary probe: does DAWA's jordstykker endpoint support bulk pagination by
kommunekode, and how many parcels are we actually talking about?

The coastal fetch used circle-search along the coastline — fine for a thin
band, but the wrong tool for "every matrikel in the region": that would be one
circle query per ~400 m, an enormous and redundant number of calls. If
kommunekode + page/per_side pagination works, fetching a whole kommune is a
handful of requests instead.
"""
import json
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "bolig-tracker/1.0 (+https://boligtracker.dk)", "Accept": "application/json"}
DAWA = "https://api.dataforsyningen.dk"


def get(url, cap=400000):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
            return r.status, r.read(cap)
    except urllib.error.HTTPError as ex:
        try:
            return ex.code, ex.read(400)
        except Exception:
            return ex.code, b""
    except Exception as ex:
        return None, f"{type(ex).__name__}: {str(ex)[:80]}".encode()


# Hørsholm (0223) — small kommune, good for counting quickly
K = "0223"

print("=== does kommunekode alone work? ===")
code, body = get(f"{DAWA}/jordstykker?kommunekode={K}&format=geojson&per_side=5&side=1")
print(f"  HTTP {code}, {len(body)} bytes")
if code == 200:
    d = json.loads(body.decode("utf-8", "replace"))
    feats = d.get("features") or []
    print(f"  {len(feats)} features returned")
    if feats:
        p = feats[0].get("properties", {})
        print("  sample properties:", json.dumps(p, ensure_ascii=False)[:500])
else:
    print(" ", body[:300].decode("utf-8", "replace"))

print("\n=== pagination: page 2 differs from page 1? ===")
c1, b1 = get(f"{DAWA}/jordstykker?kommunekode={K}&format=geojson&per_side=5&side=1")
c2, b2 = get(f"{DAWA}/jordstykker?kommunekode={K}&format=geojson&per_side=5&side=2")
if c1 == 200 and c2 == 200:
    f1 = [f["properties"].get("matrikelnr") for f in json.loads(b1)["features"]]
    f2 = [f["properties"].get("matrikelnr") for f in json.loads(b2)["features"]]
    print(f"  page1 matrikelnr: {f1}")
    print(f"  page2 matrikelnr: {f2}")
    print(f"  distinct pages: {set(f1) != set(f2) and bool(f2)}")

print("\n=== how many parcels total in Hørsholm (0223)? ===")
# walk with a moderate page size until it stops, count and time it
total, side, per_side = 0, 1, 500
t0 = time.time()
while True:
    code, body = get(f"{DAWA}/jordstykker?kommunekode={K}&format=geojson&per_side={per_side}&side={side}")
    if code != 200:
        print(f"  stopped at side {side}: HTTP {code}")
        break
    feats = json.loads(body.decode("utf-8", "replace")).get("features") or []
    total += len(feats)
    if len(feats) < per_side or side > 40:
        break
    side += 1
print(f"  {total} parcels across {side} page(s) of {per_side}, {time.time()-t0:.1f}s")

print("\n=== does the plain (non-geojson) format include registreretareal directly? ===")
code, body = get(f"{DAWA}/jordstykker?kommunekode={K}&per_side=2&side=1")
print(f"  HTTP {code}")
if code == 200:
    for rec in json.loads(body.decode("utf-8", "replace")):
        print("  keys:", sorted(rec.keys()))
        print("  sample:", json.dumps(
            {k: rec[k] for k in ("matrikelnr", "registreretareal", "ejerlav", "kommune")
             if k in rec}, ensure_ascii=False)[:400])
        break

print("\nDelete this script and its workflow once read.")
