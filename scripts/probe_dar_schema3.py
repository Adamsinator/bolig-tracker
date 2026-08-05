#!/usr/bin/env python3
"""Round 4, issue #28. Round 3 confirmed real field values but crashed on
an unhandled timeout (postnummer turned out to be a UUID relation, not a
plain 4-digit code, and querying `where: { postnummer: { eq: "2900" } }`
against a UUID-typed field hung for 30s+ instead of failing fast) — so
item 3 (resolving adgangspunkt -> coordinates) never ran. Fixed here: every
call is wrapped so one bad/slow query can't kill the rest of the script.

Two things still needed before writing the real fetch:
  - Coordinates: resolve a real adgangspunkt UUID (from round 3's sample:
    "d90e9338-0470-41bc-8ecb-6f71dd912ff0") via DAR_Adressepunkt.
  - Region scoping: DAR_Husnummer/DAR_Adresse/DAR_NavngivenVej have no
    `kommunekode` filter. The entity list (from search results) includes
    `DAR_NavngivenVejKommunedel` — a name that reads exactly like the
    street<->kommune junction table Danish streets need (since one street
    can span multiple kommuner). Testing whether that's queryable/
    filterable by kommunekode, which would let us resolve "every
    navngivenVej id in our 28 kommuner" once and filter husnumre by that
    id set — much cheaper than a nationwide unfiltered fetch.

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
SAMPLE_ADGANGSPUNKT = "d90e9338-0470-41bc-8ecb-6f71dd912ff0"
SAMPLE_POSTNUMMER_ID = "0417eeff-5346-4f42-94d2-7f61d4ba7529"


def post_graphql(query, timeout=25):
    body = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(f"{DAR}?apiKey={API_KEY}", data=body,
                                  headers={**UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as ex:
        body_txt = ""
        try:
            body_txt = ex.read().decode("utf-8", "replace")[:1500]
        except Exception:
            pass
        return ex.code, {"httpError": body_txt}
    except Exception as ex:
        return None, {"exception": f"{type(ex).__name__}: {ex}"}


def run(label, query, timeout=25):
    print(label)
    status, data = post_graphql(query, timeout)
    print(f"  status={status}")
    print(f"  {json.dumps(data, ensure_ascii=False, indent=2)[:2000]}")
    print()


def main():
    run("1) resolve a real adgangspunkt id via DAR_Adressepunkt -> position",
        f"""{{ DAR_Adressepunkt(first: 1, where: {{ id_lokalId: {{ eq: "{SAMPLE_ADGANGSPUNKT}" }} }}
              registreringstid: "{NOW}" virkningstid: "{NOW}") {{
          nodes {{ id_lokalId position }}
        }} }}""")

    run("2) resolve a real postnummer id via DAR_Postnummer -> real 4-digit code",
        f"""{{ DAR_Postnummer(first: 1, where: {{ id_lokalId: {{ eq: "{SAMPLE_POSTNUMMER_ID}" }} }}
              registreringstid: "{NOW}" virkningstid: "{NOW}") {{
          nodes {{ id_lokalId postnr navn }}
        }} }}""")

    run("3) does DAR_NavngivenVejKommunedel exist, and what does it look like unfiltered?",
        f"""{{ DAR_NavngivenVejKommunedel(first: 3
              registreringstid: "{NOW}" virkningstid: "{NOW}") {{
          pageInfo {{ hasNextPage endCursor }}
          nodes {{ id_lokalId kommune navngivenVej }}
        }} }}""")

    run("4) can DAR_NavngivenVejKommunedel be filtered by kommune?",
        f"""{{ DAR_NavngivenVejKommunedel(first: 3, where: {{ kommune: {{ eq: "0223" }} }}
              registreringstid: "{NOW}" virkningstid: "{NOW}") {{
          pageInfo {{ hasNextPage endCursor }}
          nodes {{ id_lokalId kommune navngivenVej }}
        }} }}""")

    print("done")


if __name__ == "__main__":
    main()
