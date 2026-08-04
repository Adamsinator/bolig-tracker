#!/usr/bin/env python3
"""Diagnostic-only, for #28 (DAWA sunset migration): the unauthenticated
DAR REST endpoint was already confirmed to be ID-only, no fuzzy search
(probe_dar_rest_search.py, deleted). But a server-side proxy holding
DATAFORDELER_API could instead call the authenticated DAR GraphQL
endpoint — the same one already proven for the #26 BBR work. That's only
a real migration option if GraphQL's filters support partial/fuzzy text
matching (contains/startsWith/like), not just exact equality. Never
checked. This probe does, on real vejnavn data, before proposing anything.

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


def post_graphql(query):
    body = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(f"{DAR}?apiKey={API_KEY}", data=body,
                                  headers={**UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as ex:
        try:
            return json.loads(ex.read().decode("utf-8", "replace"))
        except Exception:
            return {"httpError": str(ex)}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def try_query(label, q):
    data = post_graphql(q)
    if data.get("errors"):
        print(f"  {label}: ERROR — {data['errors'][0].get('message','')[:250]}")
    else:
        block = data.get("data", {})
        key = next(iter(block), None)
        nodes = (block.get(key) or {}).get("nodes") if key else None
        print(f"  {label}: OK — {len(nodes) if nodes is not None else '?'} results — {json.dumps(data)[:400]}")


def main():
    now = now_iso()
    # DAR_Adresse doesn't expose vejnavn directly (confirmed earlier), but
    # a related type likely does — try navngivenvej-style search first to
    # find where street names actually live, then test fuzzy operators
    # against whatever field responds.
    print("1) does DAR_Adresse (or a related type) expose anything to filter text on?")
    for entity, field in (("DAR_Adresse", "husnummer"), ("DAR_Husnummer", "husnummertekst")):
        q = (f'query {{ {entity}(first: 3 registreringstid: "{now}" virkningstid: "{now}") '
             f'{{ nodes {{ {field} }} }} }}')
        try_query(f"{entity}.{field} (sanity check, already known)", q)

    print("\n2) does a DAR_NavngivenVej (street name) entity exist, and does its "
          "name field support a 'contains'/'like' style filter operator?")
    for op in ("eq", "contains", "like", "startsWith", "match"):
        q = (f'query {{ DAR_NavngivenVej(first: 3 registreringstid: "{now}" virkningstid: "{now}" '
             f'where: {{ vejnavn: {{ {op}: "Esthersvej" }} }}) {{ nodes {{ id_lokalId vejnavn }} }} }}')
        try_query(f"DAR_NavngivenVej.vejnavn {op}", q)

    print("\n3) does DAR_Husnummer support a fuzzy filter on any address-text-ish field?")
    for field, op, val in (("husnummertekst", "eq", "45"), ("husnummertekst", "contains", "45")):
        q = (f'query {{ DAR_Husnummer(first: 3 registreringstid: "{now}" virkningstid: "{now}" '
             f'where: {{ {field}: {{ {op}: "{val}" }} }}) {{ nodes {{ id_lokalId husnummertekst }} }} }}')
        try_query(f"DAR_Husnummer.{field} {op}", q)

    print("\ndone")


if __name__ == "__main__":
    main()
