#!/usr/bin/env python3
"""Diagnostic-only: the real fetch_bbr_once.py run got stuck retrying a 400
Bad Request on every DAR_Adresse batch resolution (chunk size 200) for 3+
hours before its timeout killed it, after BBR+EBR fetch succeeded cleanly.
post_graphql() never logged the response body, so the real cause was
invisible. This probe fixes that blind spot and finds the actual breaking
point before any redesign of fetch_bbr_once.py.

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


def post_graphql(base, query, tries=3):
    body = json.dumps({"query": query}).encode("utf-8")
    for i in range(tries):
        try:
            req = urllib.request.Request(f"{base}?apiKey={API_KEY}", data=body,
                                          headers={**UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            if "errors" in data:
                print(f"  graphql errors: {json.dumps(data['errors'])[:500]}")
                return None
            return data
        except urllib.error.HTTPError as ex:
            detail = ""
            try:
                detail = ex.read().decode("utf-8", "replace")[:800]
            except Exception:
                pass
            print(f"  HTTP error (try {i+1}/{tries}): {ex} — {detail}")
            if i < tries - 1:
                time.sleep(2)
        except Exception as ex:
            print(f"  request failed (try {i+1}/{tries}): {ex}")
            if i < tries - 1:
                time.sleep(2)
    return None


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main():
    now = now_iso()

    print("1) pulling real adresseLokalId values from EBR (kommunekode 0223)…")
    q = (f'query {{ EBR_Ejendomsbeliggenhed(first: 300 registreringstid: "{now}" '
         f'virkningstid: "{now}" where: {{ kommuneinddelingKommunekode: {{ eq: "0223" }} }}) '
         f'{{ nodes {{ adresseLokalId status }} }} }}')
    data = post_graphql(EBR, q)
    if not data:
        sys.exit("could not fetch EBR sample — aborting")
    ids = [nd["adresseLokalId"] for nd in data["data"]["EBR_Ejendomsbeliggenhed"]["nodes"]
           if nd.get("status") == "gældende" and nd.get("adresseLokalId")]
    ids = sorted(set(ids))
    print(f"   got {len(ids)} real adresseLokalId values\n")
    if len(ids) < 210:
        sys.exit(f"not enough real ids ({len(ids)}) to test up to chunk 200 — aborting")

    print("2) testing DAR_Adresse batch resolution at increasing chunk sizes…")
    for size in (5, 20, 50, 100, 150, 175, 190, 200, 220, 250):
        chunk = ids[:size]
        id_list = ", ".join(f'"{x}"' for x in chunk)
        q = (f'query {{ DAR_Adresse(first: {size} registreringstid: "{now}" '
             f'virkningstid: "{now}" where: {{ id_lokalId: {{ in: [{id_list}] }} }}) '
             f'{{ nodes {{ id_lokalId husnummer }} }} }}')
        t0 = time.time()
        data = post_graphql(DAR, q, tries=1)
        dt = time.time() - t0
        if data:
            n = len(data["data"]["DAR_Adresse"]["nodes"])
            print(f"  size={size}: OK, {n} resolved, {dt:.1f}s")
        else:
            print(f"  size={size}: FAILED, {dt:.1f}s")

    print("\n3) same query shape, but without an explicit 'first' at all (in case "
          "'first' itself is the problem, not list length)…")
    chunk = ids[:200]
    id_list = ", ".join(f'"{x}"' for x in chunk)
    q = (f'query {{ DAR_Adresse(registreringstid: "{now}" '
         f'virkningstid: "{now}" where: {{ id_lokalId: {{ in: [{id_list}] }} }}) '
         f'{{ nodes {{ id_lokalId husnummer }} }} }}')
    data = post_graphql(DAR, q, tries=1)
    print("  OK" if data else "  FAILED")

    print("\ndone")


if __name__ == "__main__":
    main()
