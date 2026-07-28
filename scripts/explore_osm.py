#!/usr/bin/env python3
"""OSM exploration v2 (metro route relations + Kystbanen geometry). Throwaway."""
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
                req = urllib.request.Request(url, data=query.encode(),
                                             headers={"User-Agent": "bt-explore/2"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    return json.load(r)
            except Exception as ex:
                last = f"{url.split('//')[1].split('/')[0]}: {ex}"
        time.sleep(6)
    print("  !! all mirrors failed:", last, file=sys.stderr, flush=True)
    return None


def E(d):
    return (d or {}).get("elements", [])


BB = "55.58,12.05,55.96,12.70"

print("### A) metro route relations (route=subway) ###", flush=True)
d = q(f'[out:json][timeout:80];rel["route"="subway"]({BB});out tags;')
for r in E(d):
    t = r.get("tags", {})
    print(f"  ref={t.get('ref','')!r:5} colour={(t.get('colour') or t.get('color') or '')!r:9} "
          f"network={t.get('network','')!r:14} from={t.get('from','')!r:16} to={t.get('to','')!r:16} name={t.get('name','')!r}", flush=True)
print(f"  -> {len(E(d))} relations", flush=True)

print("\n### B) light_rail route relations that are NOT S-tog (letbane / Naerumbanen) ###", flush=True)
d = q(f'[out:json][timeout:80];rel["route"="light_rail"]({BB});out tags;')
for r in E(d):
    t = r.get("tags", {})
    nm = t.get("name", "")
    if "s-tog" in nm.lower():
        continue
    print(f"  ref={t.get('ref','')!r:6} network={t.get('network','')!r:16} name={nm!r}", flush=True)

print("\n### C) train route relations whose name mentions Kyst ###", flush=True)
d = q(f'[out:json][timeout:80];rel["route"="train"]["name"~"[Kk]ystban"]({BB});out tags;')
for r in E(d):
    t = r.get("tags", {})
    print(f"  ref={t.get('ref','')!r:6} network={t.get('network','')!r:16} name={t.get('name','')!r}", flush=True)
print(f"  -> {len(E(d))} relations", flush=True)

print("\n### D) railway=rail infra around the coastal stations (Vedbaek-Nivaa) ###", flush=True)
d = q('[out:json][timeout:80];way["railway"="rail"](55.85,12.50,55.94,12.57);out tags 50;')
c = Counter()
for w in E(d):
    t = w.get("tags", {})
    c[(t.get("usage"), t.get("service"), t.get("name"), t.get("ref"))] += 1
for k, n in c.most_common(18):
    print(f"  (usage,service,name,ref)={k}  x{n}", flush=True)
print(f"  -> {len(E(d))} ways", flush=True)
