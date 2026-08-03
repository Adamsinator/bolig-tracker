#!/usr/bin/env python3
"""Diagnostic-only, third DAGI probe: DAGI_Kommuneinddeling.geometri is
SpatialMultiPolygonEpsg25832Type — an object field needing sub-selection
(confirmed in #27). Find its actual shape via /schema SDL, then do one real
query for Hørsholm's full geometry so the coordinate format (nested arrays?
WKT string? GeoJSON?) is known before writing the reprojection code."""
import json
import os
import re
import urllib.error
import urllib.request

API_KEY = os.environ["DATAFORDELER_API"]
BASE = "https://graphql.datafordeler.dk/DAGI/v2"
UA = {"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def post_graphql(query):
    body = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(f"{BASE}?apiKey={API_KEY}", data=body,
                                  headers={**UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode("utf-8", "replace")}


def main():
    sdl = get(f"{BASE}/schema?apiKey={API_KEY}")
    stripped = re.sub(r'"""[\s\S]*?"""', "", sdl)

    for type_name in ("SpatialMultiPolygonEpsg25832Type", "SpatialPolygonEpsg25832Type",
                       "SpatialPointEpsg25832Type", "SpatialRingEpsg25832Type"):
        m = re.search(re.escape(f"type {type_name}") + r'\s*\{([^}]*)\}', stripped)
        print(f"=== {type_name} ===")
        if m:
            for line in m.group(1).splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    print(f"   {line}")
        else:
            print("   not found")
        print()

    now = "2026-08-02T12:00:00Z"
    # try the most likely shapes: wkt string, or nested coordinates
    for label, sub in [
        ("wkt", "wkt"),
        ("geoJson", "geoJson"),
        ("coordinates-flat", "coordinates"),
    ]:
        print(f"=== live query: geometri {{ {sub} }} ===")
        q = f'''
        query {{
          DAGI_Kommuneinddeling(
            first: 1
            registreringstid: "{now}"
            virkningstid: "{now}"
            where: {{ kommunekode: {{ eq: "0223" }} }}
          ) {{
            nodes {{ kommunekode navn geometri {{ {sub} }} }}
          }}
        }}
        '''
        result = post_graphql(q)
        s = json.dumps(result, ensure_ascii=False)
        print(f"   ({label}) " + (s[:800] if len(s) > 800 else s))
        print()


if __name__ == "__main__":
    main()
