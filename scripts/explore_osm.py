#!/usr/bin/env python3
"""Throwaway OSM/Overpass exploration for issues #6 (S-tog track geometry) and
#7 (amenities). Prints what OSM actually has so we can design the real fetches.
Stdlib only; robust across several mirrors with retries. Delete once the real
queries are landed."""
import json
import sys
import time
import urllib.request
from collections import Counter

MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


def q(query, tries=4):
    last = None
    for _ in range(tries):
        for url in MIRRORS:
            try:
                req = urllib.request.Request(
                    url, data=query.encode(),
                    headers={"User-Agent": "bolig-tracker-explore/1.0"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    return json.load(r)
            except Exception as ex:
                last = f"{url.split('//')[1].split('/')[0]}: {ex}"
        time.sleep(6)
    print("  !! all mirrors failed:", last, file=sys.stderr, flush=True)
    return None


def E(d):
    return (d or {}).get("elements", [])


BB = "55.58,12.05,55.96,12.70"   # whole tracked corridor

print("### A) relations whose network mentions S-tog (any route type) ###", flush=True)
d = q(f'[out:json][timeout:80];rel["network"~"[Ss].?[-_ ]?[Tt]og"]({BB});out tags;')
for r in E(d):
    t = r.get("tags", {})
    print(f"  route={t.get('route','')!r:9} ref={t.get('ref','')!r:6} "
          f"network={t.get('network','')!r:16} name={t.get('name','')!r}", flush=True)
print(f"  -> {len(E(d))} relations", flush=True)

print("\n### B) any route relation with ref in S-tog letters A/B/Bx/C/E/F/H ###", flush=True)
d = q(f'[out:json][timeout:80];rel["ref"~"^(A|B|Bx|C|E|F|H)$"]["route"]({BB});out tags;')
for r in E(d):
    t = r.get("tags", {})
    print(f"  route={t.get('route','')!r:9} ref={t.get('ref','')!r:6} "
          f"network={t.get('network','')!r:16} op={t.get('operator','')!r:16} "
          f"name={t.get('name','')!r}", flush=True)
print(f"  -> {len(E(d))} relations", flush=True)

print("\n### C) railway ways around Holte (S-tog-only branch) — tag fingerprint ###", flush=True)
d = q('[out:json][timeout:80];way["railway"="rail"](55.79,12.45,55.82,12.49);out tags 40;')
c = Counter()
for w in E(d):
    t = w.get("tags", {})
    c[(t.get("service"), t.get("usage"), t.get("network"), t.get("electrified"))] += 1
for k, n in c.most_common():
    print(f"  (service,usage,network,electrified)={k}  x{n}", flush=True)
print(f"  -> {len(E(d))} ways", flush=True)

print("\n### D) POI query test — corrected `out center N` on a small central bbox ###", flush=True)
for kind, sel in [("supermarket", '["shop"="supermarket"]'),
                  ("school", '["amenity"="school"]'),
                  ("kindergarten", '["amenity"="kindergarten"]')]:
    d = q(f'[out:json][timeout:80];nwr{sel}(55.66,12.53,55.72,12.60);out center 300;')
    e = E(d)
    named = [(x.get("tags") or {}).get("name") for x in e[:4]]
    print(f"  {kind}: {len(e)} elements; sample={named}", flush=True)
