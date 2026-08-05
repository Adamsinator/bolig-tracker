#!/usr/bin/env python3
"""Round 2, issue #28. Round 1 established two things: introspection
(__type) is disabled server-side ("Introspection is not allowed for the
current request"), and the real type is `DAR_HusnummerConnection` — a
Relay-style paginated connection, same shape as BBR_Bygning/BBR_Enhed in
fetch_bbr_lookup_once.py (`nodes { ... }`, not a flat field list). My first
guessed field names were rejected because they were missing that wrapper,
not necessarily because the names themselves were wrong.

Datafordeler's own doc pages (datafordeler.dk/dataoversigt/... and
confluence.sdfi.dk) all 403 to anonymous fetches, same as Adressevaelger's
docs did. But DAR is a standard, publicly-referenced Danish gov data model
(unlike Adressevaelger's brand-new integration guide) — WebSearch surfaced
real field names from public snippets/gists rather than gated pages:
  - DAR_Husnummer: id_lokalId, husnummertekst, adgangspunkt (a relation to
    DAR_Adressepunkt), adgangsadressebetegnelse
  - DAR_Adressepunkt: position (WKT POINT, CRS 25832 / UTM zone 32N — NOT
    WGS84 lat/lon, needs reprojecting)
  - DAR_Adresse: id_lokalId, etagebetegnelse, doerbetegnelse, and a
    "husnummer" link field (this one already confirmed live earlier this
    session, not from search)
  - proven where/pagination shape (from fetch_bbr_lookup_once.py, already
    working at scale): `Entity(first: N after: "cursor" where: {...}
    registreringstid: "..." virkningstid: "...") { pageInfo {...} nodes
    {...} }`

This verifies those specific field names live, using the proven query
shape, before writing the real multi-hour fetch — so a wrong guess costs
one fast round-trip instead of hours.

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
DAR = "https://graphql.datafordeler.dk/DAR/v3"
NOW = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def post_graphql(query):
    body = json.dumps({"query": query}).encode("utf-8")
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
        return ex.code, {"httpError": body_txt}


def run(label, query):
    print(f"{label}")
    status, data = post_graphql(query)
    print(f"  status={status}")
    print(f"  {json.dumps(data, ensure_ascii=False, indent=2)[:2500]}")
    print()


def main():
    run("1) DAR_Husnummer: id_lokalId, husnummertekst, adgangsadressebetegnelse, "
        "kommunekode, postnummer, navngivenVej, plus adgangspunkt -> position (nested relation)",
        f"""{{ DAR_Husnummer(first: 3, where: {{ kommunekode: {{ eq: "0223" }} }}
              registreringstid: "{NOW}" virkningstid: "{NOW}") {{
          pageInfo {{ hasNextPage endCursor }}
          nodes {{
            id_lokalId
            husnummertekst
            adgangsadressebetegnelse
            kommunekode
            postnummer
            navngivenVej
            adgangspunkt {{ position }}
          }}
        }} }}""")

    run("2) DAR_Adresse: id_lokalId, etagebetegnelse, doerbetegnelse, husnummer link",
        f"""{{ DAR_Adresse(first: 3, where: {{ kommunekode: {{ eq: "0223" }} }}
              registreringstid: "{NOW}" virkningstid: "{NOW}") {{
          pageInfo {{ hasNextPage endCursor }}
          nodes {{
            id_lokalId
            etagebetegnelse
            doerbetegnelse
            husnummer
          }}
        }} }}""")

    run("3) DAR_NavngivenVej: vejnavn (already confirmed working earlier this session)",
        f"""{{ DAR_NavngivenVej(first: 3, where: {{ kommunekode: {{ eq: "0223" }} }}
              registreringstid: "{NOW}" virkningstid: "{NOW}") {{
          pageInfo {{ hasNextPage endCursor }}
          nodes {{ id_lokalId vejnavn }}
        }} }}""")

    print("done")


if __name__ == "__main__":
    main()
