#!/usr/bin/env python3
"""Diagnostic-only: before writing the real BBR_Enhed fetch, confirm (1) it
filters by kommunekode the same way BBR_Bygning does — not assumed, since
BBR_Enhed's schema hasn't been checked for this field at all — and (2) a
real per-kommune record count / page latency, to size the full 28-kommune
fetch honestly instead of guessing. Same discipline as the original #26
pagination probe, and the same lesson from the DAR_Adresse chunk-size bug:
verify scale assumptions before dispatching a multi-hour run.

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
BBR = "https://graphql.datafordeler.dk/BBR/v3"


def post_graphql(query):
    body = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(f"{BBR}?apiKey={API_KEY}", data=body,
                                  headers={**UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as ex:
        try:
            return json.loads(ex.read().decode("utf-8", "replace"))
        except Exception:
            return {"httpError": str(ex)}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main():
    now = now_iso()

    print("1) does BBR_Enhed filter by kommunekode?")
    q = (f'query {{ BBR_Enhed(first: 5 registreringstid: "{now}" virkningstid: "{now}" '
         f'where: {{ kommunekode: {{ eq: "0101" }} }}) '
         f'{{ pageInfo {{ hasNextPage endCursor }} nodes {{ id_lokalId enh026EnhedensSamledeAreal adresseIdentificerer bygning }} }} }}')
    data = post_graphql(q)
    print(f"   {json.dumps(data)[:600]}")
    if not data.get("data", {}).get("BBR_Enhed"):
        print("   kommunekode filter failed — stopping here, need a different query shape")
        return

    print("\n2) real record count for København (0101) at page size 500, timing pages")
    cursor, total, page_n, t0 = None, 0, 0, time.time()
    while page_n < 6:  # cap at 6 pages (3000 records) — enough to extrapolate honestly
        after = f'after: "{cursor}"' if cursor else ""
        q2 = (f'query {{ BBR_Enhed(first: 500 {after} registreringstid: "{now}" virkningstid: "{now}" '
              f'where: {{ kommunekode: {{ eq: "0101" }} }}) '
              f'{{ pageInfo {{ hasNextPage endCursor }} nodes {{ id_lokalId }} }} }}')
        tp0 = time.time()
        data2 = post_graphql(q2)
        dt = time.time() - tp0
        block = data2.get("data", {}).get("BBR_Enhed")
        if not block:
            print(f"   page {page_n}: ERROR — {json.dumps(data2)[:300]}")
            break
        n = len(block["nodes"])
        total += n
        page_n += 1
        print(f"   page {page_n}: {n} records, {dt:.1f}s, hasNextPage={block['pageInfo']['hasNextPage']}")
        if not block["pageInfo"]["hasNextPage"]:
            print("   (reached the end — this kommune has no more pages)")
            break
        cursor = block["pageInfo"]["endCursor"]
    print(f"   fetched {total} records over {page_n} pages in {time.time()-t0:.1f}s for København alone")

    print("\ndone")


if __name__ == "__main__":
    main()
