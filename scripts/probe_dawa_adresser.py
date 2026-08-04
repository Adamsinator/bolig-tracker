#!/usr/bin/env python3
"""Diagnostic-only: the address-lookup page's autocomplete uses DAWA's
adgangsadresser/autocomplete (access-address level, no floor/door), copied
from app.js's setupGeo() where that's fine for a commute-distance radius.
For picking a SPECIFIC condo unit (e.g. "Esthersvej 45, 1. tv") that's the
wrong endpoint — a user reported it never offers unit-level suggestions.

DAWA also has adresser/autocomplete (full addresses, including etage/dør).
This confirms its real response shape before switching to it, rather than
guessing field names.

Deleted once its findings are captured, per this repo's probe convention.
"""
import json
import urllib.parse
import urllib.request

UA = {"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def main():
    q = "Esthersvej 45, 2900 Hellerup"
    print(f"1) adgangsadresser/autocomplete for {q!r} (current implementation)")
    url = "https://api.dataforsyningen.dk/adgangsadresser/autocomplete?" + urllib.parse.urlencode({"per_side": 10, "q": q})
    items = get(url)
    print(f"   {len(items)} results")
    for it in items[:5]:
        print(f"   tekst={it.get('tekst')!r}")
    if items:
        print(f"   first item full shape: {json.dumps(items[0], ensure_ascii=False)[:800]}")

    print(f"\n2) adresser/autocomplete for {q!r} (candidate replacement, unit-level)")
    url2 = "https://api.dataforsyningen.dk/adresser/autocomplete?" + urllib.parse.urlencode({"per_side": 15, "q": q})
    items2 = get(url2)
    print(f"   {len(items2)} results")
    for it in items2[:15]:
        print(f"   tekst={it.get('tekst')!r}")
    if items2:
        print(f"   first item full shape: {json.dumps(items2[0], ensure_ascii=False)[:1200]}")

    print("\n3) same query with just 'Esthersvej 45' (no postal code) on adresser/autocomplete")
    url3 = "https://api.dataforsyningen.dk/adresser/autocomplete?" + urllib.parse.urlencode({"per_side": 15, "q": "Esthersvej 45"})
    items3 = get(url3)
    print(f"   {len(items3)} results")
    for it in items3[:15]:
        print(f"   tekst={it.get('tekst')!r}")

    print("\ndone")


if __name__ == "__main__":
    main()
