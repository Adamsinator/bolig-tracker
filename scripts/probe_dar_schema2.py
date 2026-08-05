#!/usr/bin/env python3
"""Round 3, issue #28. Round 2 confirmed almost everything in one shot:
DAR_Husnummer.{id_lokalId, husnummertekst, adgangsadressebetegnelse,
postnummer, navngivenVej, adgangspunkt} and DAR_Adresse.{id_lokalId,
etagebetegnelse, doerbetegnelse, husnummer} and DAR_NavngivenVej.{id_lokalId,
vejnavn} are all real, valid field names — none of them were flagged as
errors. Only two problems surfaced:
  - `kommunekode` is not a valid field OR a valid `where` filter on
    DAR_Husnummer/DAR_Adresse/DAR_NavngivenVej (unlike BBR_Bygning/
    BBR_Enhed, where it works) — need a different way to scope the fetch
    to the 28-kommune region, or confirm we have to filter some other way.
  - `adgangspunkt` is a plain String field (an ID), not a nested object —
    "must not have a selection since type String has no subfields". So
    coordinates aren't available via `adgangspunkt { position }`; presumably
    a separate DAR_Adressepunkt query using that string as an id_lokalId
    filter, unless there's a differently-named relation field.

This round: get a few real unfiltered DAR_Husnummer rows to see actual
adgangspunkt values, then try resolving one via DAR_Adressepunkt; and try
`postnummer` as a `where` filter (Hellerup = postnr 2900, a Region
Hovedstaden postal code) as a possible region-scoping mechanism.

Deleted once its findings are captured, per this repo's probe convention.
"""
import json
import os
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
    print(label)
    status, data = post_graphql(query)
    print(f"  status={status}")
    print(f"  {json.dumps(data, ensure_ascii=False, indent=2)[:3000]}")
    print()
    return data


def main():
    d1 = run("1) unfiltered DAR_Husnummer sample — real adgangspunkt/postnummer/navngivenVej values",
              f"""{{ DAR_Husnummer(first: 3
                    registreringstid: "{NOW}" virkningstid: "{NOW}") {{
                pageInfo {{ hasNextPage endCursor }}
                nodes {{ id_lokalId husnummertekst adgangsadressebetegnelse
                         postnummer navngivenVej adgangspunkt }}
              }} }}""")

    run("2) postnummer as a where filter (2900 = Hellerup, Region Hovedstaden)",
        f"""{{ DAR_Husnummer(first: 3, where: {{ postnummer: {{ eq: "2900" }} }}
              registreringstid: "{NOW}" virkningstid: "{NOW}") {{
          pageInfo {{ hasNextPage endCursor }}
          nodes {{ id_lokalId husnummertekst postnummer }}
        }} }}""")

    ap_id = None
    try:
        nodes = d1["data"]["DAR_Husnummer"]["nodes"]
        for n in nodes:
            if n.get("adgangspunkt"):
                ap_id = n["adgangspunkt"]
                break
    except Exception as ex:
        print(f"  (couldn't extract a sample adgangspunkt id: {ex})")

    if ap_id:
        run(f"3) resolving that adgangspunkt id ({ap_id}) via DAR_Adressepunkt",
            f"""{{ DAR_Adressepunkt(first: 1, where: {{ id_lokalId: {{ eq: "{ap_id}" }} }}
                  registreringstid: "{NOW}" virkningstid: "{NOW}") {{
              nodes {{ id_lokalId position }}
            }} }}""")
    else:
        print("3) skipped — no adgangspunkt id found in step 1's sample")

    print("done")


if __name__ == "__main__":
    main()
