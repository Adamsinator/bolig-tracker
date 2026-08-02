#!/usr/bin/env python3
"""Diagnostic-only: find the DAGI/v2 entity for kommune boundaries on
Datafordeler's GraphQL, ahead of migrating fetch_boundaries() off DAWA
(#27 — DAWA sunsets 1 Oct 2026). Introspection is blocked on this endpoint
(confirmed in #27), so this reads the /schema SDL text instead, the same
technique that worked for MAT/v2.

Prints every DAGI_* type and its fields, and does one live query against
whichever type looks like the municipality entity, so both the entity name
and its geometry field shape are confirmed before writing real code.
"""
import json
import os
import re
import sys
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
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def main():
    print("1) fetching /DAGI/v2/schema ...", flush=True)
    try:
        sdl = get(f"{BASE}/schema?apiKey={API_KEY}")
    except urllib.error.HTTPError as e:
        # redact — the key is in the URL
        msg = str(e).replace(API_KEY, "***")
        sys.exit(f"schema fetch failed: {msg}")
    print(f"   {len(sdl)} bytes of SDL")

    # strip """..."""  description blocks before matching, same fix as MAT/v2
    stripped = re.sub(r'"""[\s\S]*?"""', "", sdl)

    print("\n2) DAGI_* types found:")
    type_names = sorted(set(re.findall(r'\btype\s+(DAGI_\w+)', stripped)))
    for t in type_names:
        print(f"   {t}")

    candidates = [t for t in type_names if "kommune" in t.lower()]
    print(f"\n3) candidates matching 'kommune': {candidates}")

    print("\n3b) full field list for every DAGI_* type (so a wrong 'kommune' guess "
          "doesn't need a second probe round):")
    all_fields = {}
    for t in type_names:
        m = re.search(re.escape(f"type {t}") + r'\s*\{([^}]*)\}', stripped)
        fields = []
        if m:
            for line in m.group(1).splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                fname = line.split(":")[0].split("(")[0].strip()
                if fname:
                    fields.append(fname)
        all_fields[t] = fields
        print(f"   {t}: {fields}")

    target = candidates[0] if candidates else None
    if not target:
        print("\n   no obvious match among DAGI_* type names — inspect the full list above")
        return
    fields = all_fields[target]
    print(f"\n4) using {target} for the live query below")

    # bitemporal query, same recipe as the confirmed BBR_Bygning call in #27
    print(f"\n5) live query against {target} for Hørsholm (kommunekode 0223) ...")
    now = "2026-08-02T12:00:00Z"
    field_sel = " ".join(f for f in fields if "(" not in f)[:2000] or "kommunekode navn"
    query = f'''
    query {{
      {target}(
        first: 2
        registreringstid: "{now}"
        virkningstid: "{now}"
      ) {{
        nodes {{ {field_sel} }}
      }}
    }}
    '''
    try:
        data = post_graphql(query)
        print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])
    except Exception as ex:
        print(f"   live query failed: {str(ex).replace(API_KEY, '***')}")


if __name__ == "__main__":
    main()
