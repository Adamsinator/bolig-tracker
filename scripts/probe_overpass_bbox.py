#!/usr/bin/env python3
"""Diagnostic-only: exercise the widened CORRIDOR_BBOX (region expansion, #24)
against the real Overpass mirrors and report element counts / timing / payload
size per layer, without writing anything. Deleted once the expansion either
ships or gets tiled — this is a one-time go/no-go check, not part of the build.
"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, "scripts")
from build_data import OVERPASS_MIRRORS, CORRIDOR_BBOX, TRANSIT_BBOX  # noqa: E402

QUERIES = {
    "transit_metro_stations": ('[out:json][timeout:60];node["station"="subway"]({s},{w},{n},{e});out body;', TRANSIT_BBOX),
    "transit_metro_lines": ('[out:json][timeout:90];rel["route"="subway"]({s},{w},{n},{e});out geom;', TRANSIT_BBOX),
    "rail_light_rail_all": ('[out:json][timeout:120];rel["route"="light_rail"]({s},{w},{n},{e});out geom;', CORRIDOR_BBOX),
    "rail_kystbanen": ('[out:json][timeout:120];way["railway"="rail"]["name"="Kystbanen"]({s},{w},{n},{e});out geom;', CORRIDOR_BBOX),
    "stog_stations": ('[out:json][timeout:120];rel["route"="light_rail"]["name"~"S-tog"]({s},{w},{n},{e});node(r)->.n;.n out body;', CORRIDOR_BBOX),
    "pois": ('[out:json][timeout:120];(nwr["shop"="supermarket"]({s},{w},{n},{e});nwr["amenity"="school"]({s},{w},{n},{e});'
             'nwr["amenity"="kindergarten"]({s},{w},{n},{e});nwr["amenity"="childcare"]({s},{w},{n},{e}););out center 8000;', CORRIDOR_BBOX),
    "geo_motorway": ('[out:json][timeout:120];(way["highway"~"^(motorway|trunk)$"]({s},{w},{n},{e}););out geom;', CORRIDOR_BBOX),
    "geo_coastline": ('[out:json][timeout:120];(way["natural"="coastline"]({s},{w},{n},{e}););out geom;', CORRIDOR_BBOX),
    "geo_lakes": ('[out:json][timeout:120];(way["natural"="water"]["name"]({s},{w},{n},{e}););out geom;', CORRIDOR_BBOX),
    "geo_green": ('[out:json][timeout:120];(way["landuse"="forest"]({s},{w},{n},{e});way["natural"="wood"]({s},{w},{n},{e});'
                  'way["leisure"="park"]({s},{w},{n},{e}););out geom;', CORRIDOR_BBOX),
}


def run_one(name, tmpl, bbox):
    s, w, n, e = bbox
    q = tmpl.format(s=s, w=w, n=n, e=e)
    for mirror in OVERPASS_MIRRORS:
        t0 = time.time()
        try:
            req = urllib.request.Request(mirror, data=q.encode("utf-8"),
                                          headers={"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"})
            with urllib.request.urlopen(req, timeout=150) as r:
                raw = r.read()
            dt = time.time() - t0
            data = json.loads(raw)
            n_el = len(data.get("elements", []))
            print(f"  {name:24s} via {mirror.split('/')[2]:32s} OK   {dt:6.1f}s  {len(raw)/1e6:6.2f}MB  {n_el:6d} elements", flush=True)
            return
        except Exception as ex:
            dt = time.time() - t0
            print(f"  {name:24s} via {mirror.split('/')[2]:32s} FAIL {dt:6.1f}s  {ex}", flush=True)
    print(f"  {name:24s} ALL MIRRORS FAILED", flush=True)


def main():
    print(f"CORRIDOR_BBOX = {CORRIDOR_BBOX}  (old was 55.58,12.05,55.96,12.70)")
    print(f"TRANSIT_BBOX  = {TRANSIT_BBOX}\n")
    t0 = time.time()
    for name, (tmpl, bbox) in QUERIES.items():
        run_one(name, tmpl, bbox)
    print(f"\ntotal wall time: {(time.time()-t0)/60:.1f} min")


if __name__ == "__main__":
    main()
