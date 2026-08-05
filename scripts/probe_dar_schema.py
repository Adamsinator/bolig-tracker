#!/usr/bin/env python3
"""Diagnostic-only, for issue #28. Adressevaelger (the official DAWA
replacement) turned out to have real gaps when tested live: no coordinates,
no unit/floor-level breakdown in its /soeg results. Rather than keep
depending on a third-party live API at all, the plan is now to fetch the
full DAR address register ourselves (street names, house numbers,
coordinates, floor/door labels) via Datafordeler — the same server-side
DATAFORDELER_API key already used for BBR_Bygning/BBR_Enhed in
fetch_bbr_lookup_once.py — and ship it as a static file like bbr_lookup.json.

This checks the exact field names before writing that fetch, so we don't
repeat the #26 mistake of assuming a field/limit and burning hours finding
out it's wrong. Specifically:
  - DAR_Husnummer: does it exist, what are its house-number-text and
    coordinate (x/y) field names, does kommunekode filtering work on it
    (confirmed working on BBR_Enhed already), and how does it link to
    DAR_NavngivenVej (street name)?
  - DAR_Adresse: what are the floor ("etage") and door ("dør") label field
    names, and how does it link to its parent DAR_Husnummer?

Deleted once its findings are captured, per this repo's probe convention.
"""
import json
import os
import sys
import urllib.error
import urllib.request

API_KEY = os.environ["DATAFORDELER_API"]
UA = {"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"}
# confirmed via git history of this session's earlier (deleted) DAR probes —
# do not guess a different path/version.
DAR = "https://graphql.datafordeler.dk/DAR/v3"


def post_graphql(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(f"{DAR}?apiKey={API_KEY}", data=body,
                                  headers={**UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as ex:
        body_txt = ""
        try:
            body_txt = ex.read().decode("utf-8", "replace")[:2000]
        except Exception:
            pass
        print(f"  HTTP {ex.code} error body: {body_txt}", file=sys.stderr)
        return ex.code, None


def introspect_type(type_name):
    q = """
    query($t: String!) {
      __type(name: $t) {
        name
        fields { name type { name kind ofType { name kind } } }
      }
    }
    """
    status, data = post_graphql(q, {"t": type_name})
    print(f"  status={status}")
    if not data:
        print("  no data")
        return
    if data.get("errors"):
        print(f"  errors={data['errors']}")
    t = (data.get("data") or {}).get("__type")
    if not t:
        print(f"  __type({type_name}) -> null (does not exist)")
        return
    print(f"  {type_name} fields:")
    for f in t.get("fields") or []:
        tt = f["type"]
        tn = tt.get("name") or (tt.get("ofType") or {}).get("name")
        print(f"    {f['name']}: {tn}")


def main():
    print("1) DAR_Husnummer schema")
    introspect_type("DAR_Husnummer")

    print("\n2) DAR_Adresse schema")
    introspect_type("DAR_Adresse")

    print("\n3) DAR_NavngivenVej schema (already confirmed to exist earlier this session — "
          "re-checking fields for completeness)")
    introspect_type("DAR_NavngivenVej")

    print("\n4) a real, small live query: 5 DAR_Husnummer rows for kommunekode 223 (Hørsholm), "
          "whatever fields look most promising from the introspection above")
    q = """
    { DAR_Husnummer(kommunekode: ["223"], size: 5) {
        id_lokalId
        husnummertekst
        adgangspunkt_x
        adgangspunkt_y
        postnr
        vejnavn
    } }
    """
    status, data = post_graphql(q)
    print(f"   status={status}")
    print(f"   {json.dumps(data, ensure_ascii=False, indent=2)[:1500] if data else 'no data'}")

    print("\ndone")


if __name__ == "__main__":
    main()
