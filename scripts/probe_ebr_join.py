#!/usr/bin/env python3
"""Diagnostic-only, follow-up for #26: BBR_Bygning has no bfeNummer field of
its own (confirmed in the prior probe), so the join from a boligsiden
listing's bfeNumbers to an actual building record needs a bridge. EBR
(EBR_Ejendomsbeliggenhed) is the confirmed-reachable register named for this
in #27's register map. Checks its field list, and separately tries filtering
BBR_Bygning directly by a jordstykke/grund reference in case that path is
simpler than going through EBR at all."""
import json
import os
import re
import urllib.error
import urllib.request

API_KEY = os.environ["DATAFORDELER_API"]
UA = {"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def post_graphql(base, query):
    body = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(f"{base}?apiKey={API_KEY}", data=body,
                                  headers={**UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode("utf-8", "replace")}


def dump_type_fields(sdl, type_name):
    stripped = re.sub(r'"""[\s\S]*?"""', "", sdl)
    m = re.search(re.escape(f"type {type_name}") + r'\s*\{([^}]*)\}', stripped)
    if not m:
        print(f"   {type_name}: not found")
        return []
    fields = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            print(f"   {line}")
            fname = line.split(":")[0].split("(")[0].strip()
            if fname:
                fields.append(fname)
    return fields


def main():
    print("=== 1) EBR/v1 schema — EBR_Ejendomsbeliggenhed fields ===", flush=True)
    ebr_base = "https://graphql.datafordeler.dk/EBR/v1"
    try:
        sdl = get(f"{ebr_base}/schema?apiKey={API_KEY}")
        fields = dump_type_fields(sdl, "EBR_Ejendomsbeliggenhed")
    except urllib.error.HTTPError as e:
        print(f"   schema fetch failed: {str(e).replace(API_KEY, '***')}")
        fields = []

    bfe_like = [f for f in fields if re.search(r'bfe|ejendomsnummer', f, re.I)]
    print(f"\n   BFE-ish fields on EBR_Ejendomsbeliggenhed: {bfe_like}")

    now = "2026-08-02T12:00:00Z"
    if bfe_like:
        print(f"\n=== 2) live EBR query filtering by {bfe_like[0]} ===")
        # Sofievej 11, Vedbæk — the 125M kr waterfront listing, so the BFE
        # number is easy to cross-check by eye if this works
        q = f'''
        query {{
          EBR_Ejendomsbeliggenhed(
            first: 3
            registreringstid: "{now}"
            virkningstid: "{now}"
          ) {{
            nodes {{ {" ".join(f for f in fields if "(" not in f)[:1500]} }}
          }}
        }}
        '''
        result = post_graphql(ebr_base, q)
        print(json.dumps(result, ensure_ascii=False, indent=2)[:3000])

    print("\n=== 3) does BBR_Bygning itself filter on jordstykke/grund? ===")
    bbr_base = "https://graphql.datafordeler.dk/BBR/v3"
    q2 = f'''
    query {{
      BBR_Bygning(
        first: 2
        registreringstid: "{now}"
        virkningstid: "{now}"
        where: {{ kommunekode: {{ eq: "0223" }} }}
      ) {{
        nodes {{ kommunekode husnummer grund jordstykke ejerlejlighed byg026Opfoerelsesaar byg032YdervaeggensMateriale byg033Tagdaekningsmateriale byg056Varmeinstallation }}
      }}
    }}
    '''
    print(json.dumps(post_graphql(bbr_base, q2), ensure_ascii=False, indent=2)[:3000])


if __name__ == "__main__":
    main()
