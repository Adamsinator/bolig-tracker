#!/usr/bin/env python3
"""Diagnostic-only, for the address-lookup/valuation feature (post-#26) and
the DAWA-sunset migration (#28): can a client resolve a partially-typed
address to a BFE number WITHOUT the Datafordeler API key (which can't be
exposed in browser JS)?

Issue #26's survey found services.datafordeler.dk/DAR/DAR/3.0.0/REST/husnummer
answers with no auth (HTTP 200) — but only confirmed the endpoint responds,
not whether it supports fuzzy/partial address-text search (vs. exact-ID
lookup only). This probe checks that, and also introspects the authenticated
GraphQL DAR_Adresse type to see what fields exist, in case the REST surface
mirrors it.

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
DAR_GQL = "https://graphql.datafordeler.dk/DAR/v3"
REST_BASE = "https://services.datafordeler.dk/DAR/DAR/3.0.0/rest"


def get(url, headers=None, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
                return r.status, body
        except urllib.error.HTTPError as ex:
            body = ""
            try:
                body = ex.read().decode("utf-8", "replace")
            except Exception:
                pass
            return ex.code, body
        except Exception as ex:
            if i == tries - 1:
                return None, str(ex)
            time.sleep(1)


def post_graphql(query):
    body = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(f"{DAR_GQL}?apiKey={API_KEY}", data=body,
                                  headers={**UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as ex:
        print(f"  HTTPError: {ex} — {ex.read().decode('utf-8','replace')[:500]}")
        return None


def main():
    print("1) unauthenticated REST /husnummer — no query params (confirmed 200 in #26)")
    status, body = get(f"{REST_BASE}/husnummer")
    print(f"   status={status}")
    print(f"   body[:600]: {body[:600]}\n")

    print("2) unauthenticated REST /husnummer — try common address-search query params")
    candidates = [
        {"vejnavn": "Sofievej"},
        {"Vejnavn": "Sofievej"},
        {"husnummertekst": "11"},
        {"q": "Sofievej 11"},
        {"soegetekst": "Sofievej"},
        {"navn": "Sofievej"},
    ]
    for params in candidates:
        qs = urllib.parse.urlencode(params)
        status, body = get(f"{REST_BASE}/husnummer?{qs}")
        print(f"   params={params} -> status={status}")
        print(f"     body[:300]: {body[:300]}\n")

    print("3) same probes against /adresse and /vejstykke resources")
    for resource in ("adresse", "vejstykke", "navngivenvej"):
        status, body = get(f"{REST_BASE}/{resource}?vejnavn=Sofievej")
        print(f"   /{resource}?vejnavn=Sofievej -> status={status}")
        print(f"     body[:300]: {body[:300]}\n")

    print("4) does the REST service publish an OpenAPI/Swagger doc?")
    for path in ("openapi.json", "swagger.json", "api-docs", "v3/api-docs", "swagger/v1/swagger.json"):
        status, body = get(f"{REST_BASE.rsplit('/rest', 1)[0]}/{path}")
        print(f"   {path} -> status={status}, len={len(body) if body else 0}")

    print("\n5) authenticated GraphQL introspection on DAR_Adresse fields "
          "(server-side reference only — confirms what's queryable at all)")
    q = ('query { __type(name: "DAR_Adresse") { fields { name type { name kind } } } }')
    data = post_graphql(q)
    if data and data.get("data", {}).get("__type"):
        names = [f["name"] for f in data["data"]["__type"]["fields"]]
        print(f"   DAR_Adresse fields ({len(names)}): {names}")
    else:
        print(f"   introspection failed or empty: {data}")

    print("\ndone")


if __name__ == "__main__":
    main()
