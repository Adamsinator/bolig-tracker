#!/usr/bin/env python3
"""Diagnostic-only, two questions for #26 (using the Datafordeler account to
pull real BBR building attributes — roof, walls, heating, extensions — as
hedonic model features):

1. What does BBR_Bygning actually expose beyond the 5 fields already
   confirmed working in #27 (kommunekode, husnummer, id_lokalId,
   byg007Bygningsnummer, grund)? Read via /schema SDL, same technique as
   the DAGI probe — introspection is blocked on this endpoint.

2. Can an ACTIVE boligsiden listing be joined to a BFE/BBR record at all?
   Boliga's SOLD records carry bfEnr (confirmed in #26), but nothing in
   build_data.py's trim() currently reads anything BFE-like off a
   boligsiden case — never checked whether the raw payload has one.
   Fetches one real case and prints every top-level key.

No commit step. Findings go in the issue, not committed code, until the
join path is confirmed.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API_KEY = os.environ["DATAFORDELER_API"]
BASE = "https://graphql.datafordeler.dk/BBR/v3"
UA = {"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def part1_bbr_schema():
    print("=== 1) BBR/v3 schema — BBR_Bygning fields ===", flush=True)
    try:
        sdl = get(f"{BASE}/schema?apiKey={API_KEY}")
    except urllib.error.HTTPError as e:
        print(f"   schema fetch failed: {str(e).replace(API_KEY, '***')}")
        return
    stripped = re.sub(r'"""[\s\S]*?"""', "", sdl)
    m = re.search(r'type\s+BBR_Bygning\s*\{([^}]*)\}', stripped)
    if not m:
        print("   BBR_Bygning type not found in SDL")
        return
    fields = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fname = line.split(":")[0].split("(")[0].strip()
        if fname:
            fields.append(fname)
            print(f"   {line}")
    # fields likely to matter for a hedonic model: roof, walls, heating, extensions
    interesting = [f for f in fields if re.search(
        r'tag|ydervæg|varme|ombygning|udnyt|etage|opvarm', f, re.I)]
    print(f"\n   candidate hedonic fields (roof/wall/heating/extension-ish): {interesting}")


def part2_boligsiden_case():
    print("\n=== 2) One raw boligsiden case — looking for a BFE/estate join key ===", flush=True)
    qs = urllib.parse.urlencode({
        "addressTypes": "condo", "municipalities": "koebenhavn", "per_page": 1, "page": 1,
    })
    req = urllib.request.Request(
        f"https://api.boligsiden.dk/search/cases?{qs}",
        headers={"Accept": "application/json", "User-Agent": UA["User-Agent"]},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
    except Exception as ex:
        print(f"   boligsiden fetch failed: {ex}")
        return
    cases = data.get("cases") or []
    if not cases:
        print("   no cases returned")
        return
    case = cases[0]
    print(f"   top-level keys ({len(case)}): {sorted(case.keys())}")
    bfe_like = [k for k in case if re.search(r'bfe|estate|ejendom|bbr|matrikel', k, re.I)]
    print(f"   BFE/estate/BBR/matrikel-ish keys: {bfe_like}")
    for k in bfe_like:
        print(f"     {k} = {case[k]!r}")
    addr = case.get("address") or {}
    print(f"   address sub-object keys: {sorted(addr.keys()) if isinstance(addr, dict) else addr}")


if __name__ == "__main__":
    part1_bbr_schema()
    part2_boligsiden_case()
