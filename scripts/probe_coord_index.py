#!/usr/bin/env python3
"""Diagnostic-only: scoping a static coordinate -> BFE index for the
address-lookup/valuation feature, so a typed address (already geocoded to
lat/lon by the existing DAWA autocomplete) can resolve to a BFE number
entirely client-side, no backend, no exposed API key.

GraphQL introspection is disabled server-side (confirmed in the DAR REST
probe), so this leans on GraphQL's field-suggestion errors instead: query
EBR_Ejendomsbeliggenhed and DAR_Adresse with plausible coordinate field
names and read what the "did you mean" suggestions reveal about the real
schema. Also checks whether a separate DAR_Husnummer entity exists (EBR
only gives husnummerLokalId as a bridge, not necessarily coordinates
itself).

Deleted once its findings are captured, per this repo's probe convention.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_KEY = os.environ["DATAFORDELER_API"]
UA = {"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"}
EBR = "https://graphql.datafordeler.dk/EBR/v1"
DAR = "https://graphql.datafordeler.dk/DAR/v3"


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


def try_field(base, entity, field, extra_args=""):
    now = now_iso()
    q = (f'query {{ {entity}(first: 1 {extra_args} registreringstid: "{now}" '
         f'virkningstid: "{now}") {{ nodes {{ {field} }} }} }}')
    data = post_graphql(base, q)
    if data.get("errors"):
        msg = data["errors"][0].get("message", "")
        print(f"  {entity}.{field}: ERROR — {msg[:200]}")
    elif data.get("data"):
        print(f"  {entity}.{field}: OK — {json.dumps(data['data'])[:300]}")
    else:
        print(f"  {entity}.{field}: unexpected — {json.dumps(data)[:300]}")


def main():
    print("1) does EBR_Ejendomsbeliggenhed carry any coordinate/geometry field?")
    for f in ("wgs84koordinat", "etrs89koordinat", "koordinat", "geometri",
              "adgangspunkt", "x", "y", "position", "geom"):
        try_field(EBR, "EBR_Ejendomsbeliggenhed", f)

    print("\n2) does DAR_Adresse carry any coordinate/geometry field "
          "(beyond id_lokalId/husnummer, already known-working)?")
    for f in ("wgs84koordinat", "etrs89koordinat", "koordinat", "geometri",
              "adgangspunkt", "x", "y", "vejnavn", "husnummertekst", "postnr"):
        try_field(DAR, "DAR_Adresse", f)

    print("\n3) does a separate DAR_Husnummer entity exist at the top level?")
    now = now_iso()
    q = (f'query {{ DAR_Husnummer(first: 1 registreringstid: "{now}" '
         f'virkningstid: "{now}") {{ nodes {{ id_lokalId }} }} }}')
    data = post_graphql(DAR, q)
    print(f"  DAR_Husnummer: {json.dumps(data)[:400]}")

    print("\ndone")


if __name__ == "__main__":
    main()
