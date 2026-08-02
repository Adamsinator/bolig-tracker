#!/usr/bin/env python3
"""Diagnostic-only: figure out how the region's local/regional trains outside
S-tog are tagged in OSM, so fetch_transit()'s lokalbane query (currently only
route=light_rail) can be broadened correctly. Region expansion (#24) added
Halsnæs and the rest of Gribskov, which should have Frederiksværkbanen
(Hillerød-Frederiksværk-Hundested) and the Gilleleje/Tisvildeleje branch of
"Lille Nord" — neither showed up in the first 28-kommune build's lokalbane
list (only Nærumbanen + Gribskovbanen, same 2 as before the expansion).
Prints every route=light_rail AND route=train relation in CORRIDOR_BBOX with
name/ref/operator, so the actual tagging is known before changing the query."""
import json
import sys
import urllib.request

sys.path.insert(0, "scripts")
from build_data import OVERPASS_MIRRORS, CORRIDOR_BBOX  # noqa: E402


def overpass(query):
    for mirror in OVERPASS_MIRRORS:
        try:
            req = urllib.request.Request(mirror, data=query.encode("utf-8"),
                                          headers={"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"})
            with urllib.request.urlopen(req, timeout=150) as r:
                return json.loads(r.read())
        except Exception as ex:
            print(f"  via {mirror.split('/')[2]} failed: {ex}", flush=True)
    return None


def main():
    s, w, n, e = CORRIDOR_BBOX
    print(f"CORRIDOR_BBOX = {CORRIDOR_BBOX}\n")
    for route_type in ("light_rail", "train"):
        print(f"=== route={route_type} ===")
        data = overpass(f'[out:json][timeout:120];rel["route"="{route_type}"]({s},{w},{n},{e});out tags;')
        rels = (data or {}).get("elements", [])
        if not rels:
            print("  (none found)")
        for r in rels:
            t = r.get("tags", {}) or {}
            print(f"  id={r['id']} ref={t.get('ref')!r} name={t.get('name')!r} "
                  f"operator={t.get('operator')!r} network={t.get('network')!r}")
        print()


if __name__ == "__main__":
    main()
