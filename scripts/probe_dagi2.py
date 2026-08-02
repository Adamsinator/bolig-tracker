#!/usr/bin/env python3
"""Diagnostic-only, follow-up to probe_dagi.py: DAGI_Kommuneinddeling is
confirmed (kommunekode, navn, geometri, id_lokalId, regionLokalid + the
usual bitemporal fields). Two things still needed before writing real code:
the GraphQL type of `geometri` (scalar string vs. object needing its own
sub-selection — the first probe's blind field-join 400'd), and a working
filtered query for one real kommune."""
import json
import os
import re
import sys
import urllib.error
import urllib.request

API_KEY = os.environ["DATAFORDELER_API"]
BASE = "https://graphql.datafordeler.dk/DAGI/v2"
UA = {"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def post_graphql(query):
    body = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(f"{BASE}?apiKey={API_KEY}", data=body,
                                  headers={**UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return {"http_error": e.code, "body": body}


def main():
    sdl = get(f"{BASE}/schema?apiKey={API_KEY}")
    stripped = re.sub(r'"""[\s\S]*?"""', "", sdl)
    m = re.search(r'type\s+DAGI_Kommuneinddeling\s*\{([^}]*)\}', stripped)
    print("=== raw field:Type lines for DAGI_Kommuneinddeling ===")
    if m:
        for line in m.group(1).splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                print(f"   {line}")

    now = "2026-08-02T12:00:00Z"

    print("\n=== query 1: scalars only (kommunekode, navn), filtered to Hørsholm ===")
    q1 = f'''
    query {{
      DAGI_Kommuneinddeling(
        first: 3
        registreringstid: "{now}"
        virkningstid: "{now}"
        where: {{ kommunekode: {{ eq: "0223" }} }}
      ) {{
        nodes {{ kommunekode navn id_lokalId }}
      }}
    }}
    '''
    print(json.dumps(post_graphql(q1), ensure_ascii=False, indent=2)[:3000])

    print("\n=== query 2: add geometri bare (expect an error naming valid subfields if it's an object) ===")
    q2 = f'''
    query {{
      DAGI_Kommuneinddeling(
        first: 1
        registreringstid: "{now}"
        virkningstid: "{now}"
        where: {{ kommunekode: {{ eq: "0223" }} }}
      ) {{
        nodes {{ kommunekode navn geometri }}
      }}
    }}
    '''
    print(json.dumps(post_graphql(q2), ensure_ascii=False, indent=2)[:3000])


if __name__ == "__main__":
    main()
