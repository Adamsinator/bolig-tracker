#!/usr/bin/env python3
"""Diagnostic-only: scoping full auto-fill (size/rooms/year) for the
address-lookup page, on top of the floor/type auto-fill already shipped.

Two open questions, both load-bearing for whether this is even worth
building:

1) Does DAWA's adresser/autocomplete return an address ID (`adresse.id`,
   `adresse.adgangsadresseid`) that's directly usable as a DAR
   id_lokalId? Assumed (DAWA is built on DAR data) but never verified —
   picks a real address from DAWA, then checks whether DAR_Adresse /
   DAR_Husnummer resolve that exact ID.

2) Does BBR_Enhed (the dwelling-unit register — housing area, room count;
   distinct from BBR_Bygning, which only has building-level year/walls/
   roof/heating, already fetched for #26) exist and carry those fields,
   and at what real fill rate? GraphQL introspection is disabled, so this
   checks existence first, then a broad net of plausible field names
   rather than betting on one guess.

Deleted once its findings are captured, per this repo's probe convention.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_KEY = os.environ["DATAFORDELER_API"]
UA = {"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"}
DAR = "https://graphql.datafordeler.dk/DAR/v3"
BBR = "https://graphql.datafordeler.dk/BBR/v3"
EBR = "https://graphql.datafordeler.dk/EBR/v1"


def dawa_get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def post_graphql(base, query):
    body = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(f"{base}?apiKey={API_KEY}", data=body,
                                  headers={**UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as ex:
        try:
            return json.loads(ex.read().decode("utf-8", "replace"))
        except Exception:
            return {"httpError": str(ex)}
    except Exception as ex:
        return {"error": str(ex)}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def try_field(base, entity, field):
    now = now_iso()
    q = (f'query {{ {entity}(first: 1 registreringstid: "{now}" '
         f'virkningstid: "{now}") {{ nodes {{ {field} }} }} }}')
    data = post_graphql(base, q)
    if data.get("errors"):
        print(f"  {entity}.{field}: ERROR — {data['errors'][0].get('message','')[:180]}")
        return False
    print(f"  {entity}.{field}: OK — {json.dumps(data.get('data'))[:300]}")
    return True


def main():
    print("=== Part 1: does a DAWA-returned address ID resolve in DAR? ===")
    q = "Esthersvej 45, 1. tv, 2900 Hellerup"
    url = "https://api.dataforsyningen.dk/adresser/autocomplete?" + urllib.parse.urlencode({"per_side": 1, "q": q})
    items = dawa_get(url)
    if not items:
        print("  no DAWA result — aborting part 1")
    else:
        a = items[0]["adresse"]
        addr_id, access_id = a["id"], a["adgangsadresseid"]
        print(f"  DAWA gave: adresse.id={addr_id}  adgangsadresseid={access_id}")

        now = now_iso()
        q1 = (f'query {{ DAR_Adresse(first: 1 registreringstid: "{now}" virkningstid: "{now}" '
              f'where: {{ id_lokalId: {{ eq: "{addr_id}" }} }}) {{ nodes {{ id_lokalId husnummer }} }} }}')
        r1 = post_graphql(DAR, q1)
        print(f"  DAR_Adresse.id_lokalId == DAWA adresse.id? -> {json.dumps(r1)[:400]}")

        q2 = (f'query {{ DAR_Husnummer(first: 1 registreringstid: "{now}" virkningstid: "{now}" '
              f'where: {{ id_lokalId: {{ eq: "{access_id}" }} }}) {{ nodes {{ id_lokalId }} }} }}')
        r2 = post_graphql(DAR, q2)
        print(f"  DAR_Husnummer.id_lokalId == DAWA adgangsadresseid? -> {json.dumps(r2)[:400]}")

    print("\n=== Part 2: does BBR_Enhed exist, and does it carry area/room fields? ===")
    now = now_iso()
    q3 = f'query {{ BBR_Enhed(first: 1 registreringstid: "{now}" virkningstid: "{now}") {{ nodes {{ id_lokalId }} }} }}'
    r3 = post_graphql(BBR, q3)
    print(f"  BBR_Enhed exists? -> {json.dumps(r3)[:400]}")
    if not r3.get("data", {}).get("BBR_Enhed"):
        print("  BBR_Enhed doesn't exist or errored — stopping here")
        return

    print("\n  trying join-key candidates (does a unit link back to husnummer/adresse?):")
    for f in ("husnummer", "adresseIdentificerer", "adresse", "bygning", "bygningsId", "id_lokalId"):
        try_field(BBR, "BBR_Enhed", f)

    print("\n  trying area/room field-name candidates:")
    for f in ("enh026EnhedensSamledeAreal", "enh027EnhedensAndelAfBoligareal", "arealTilBeboelse",
              "boligareal", "samletAreal", "arealSamlet", "antalVaerelser", "vaerelser",
              "antalVarelser", "enh020EnhedensAnvendelse", "anvendelseskode", "boligtype"):
        try_field(BBR, "BBR_Enhed", f)

    print("\ndone")


if __name__ == "__main__":
    main()
