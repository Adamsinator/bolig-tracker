#!/usr/bin/env python3
"""Diagnostic-only, before committing to a region-wide BBR fetch for #26:

1. Does `where: { bestemtFastEjendomBFENr: { eq: ... } }` actually filter
   EBR_Ejendomsbeliggenhed, using a REAL bfeNumbers value pulled live off an
   actual boligsiden listing (not a guessed number)?
2. When EBR's husnummerLokalId is null (common in the earlier sample — only
   adresseLokalId was populated for unit-level addresses), does DAR_Adresse
   carry a field that resolves adresseLokalId -> the husnummer-level ID that
   BBR_Bygning.husnummer actually matches on?
3. Real field-population rate for BBR_Bygning's hedonic-candidate fields
   across a full kommune (not just 2 sample rows) — is this data actually
   usable, or mostly null?
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

API_KEY = os.environ["DATAFORDELER_API"]
UA = {"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"}
NOW = "2026-08-03T12:00:00Z"


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def post_graphql(base, query):
    body = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(f"{base}?apiKey={API_KEY}", data=body,
                                  headers={**UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode("utf-8", "replace")}


def main():
    print("=== 1) pull one real listing, get a real bfeNumbers value ===", flush=True)
    qs = urllib.parse.urlencode({
        "addressTypes": "villa", "municipalities": "hoersholm", "per_page": 1, "page": 1,
    })
    with urllib.request.urlopen(urllib.request.Request(
            f"https://api.boligsiden.dk/search/cases?{qs}", headers=UA), timeout=60) as r:
        case = json.load(r)["cases"][0]
    bfe = (case.get("address") or {}).get("bfeNumbers")
    print(f"   listing: {case.get('address', {}).get('road')} {case.get('address', {}).get('houseNumber')}, bfeNumbers={bfe}")

    if not bfe:
        print("   no bfeNumbers on this listing, trying a few more...")
        return
    bfe_val = bfe[0] if isinstance(bfe, list) else bfe

    print(f"\n=== 2) filter EBR by bestemtFastEjendomBFENr = {bfe_val} ===")
    ebr_base = "https://graphql.datafordeler.dk/EBR/v1"
    q = f'''
    query {{
      EBR_Ejendomsbeliggenhed(
        first: 5
        registreringstid: "{NOW}"
        virkningstid: "{NOW}"
        where: {{ bestemtFastEjendomBFENr: {{ eq: "{bfe_val}" }} }}
      ) {{
        nodes {{ bestemtFastEjendomBFENr adresseLokalId husnummerLokalId kommuneinddelingKommunekode status }}
      }}
    }}
    '''
    ebr_result = post_graphql(ebr_base, q)
    print(json.dumps(ebr_result, ensure_ascii=False, indent=2)[:2000])

    addr_lokal_id = None
    try:
        nodes = ebr_result["data"]["EBR_Ejendomsbeliggenhed"]["nodes"]
        if nodes:
            addr_lokal_id = nodes[0].get("adresseLokalId")
    except Exception:
        pass

    if addr_lokal_id:
        print(f"\n=== 3) DAR_Adresse schema — does it resolve adresseLokalId -> husnummer? ===")
        dar_base = "https://graphql.datafordeler.dk/DAR/v3"
        try:
            sdl = get(f"{dar_base}/schema?apiKey={API_KEY}")
            stripped = re.sub(r'"""[\s\S]*?"""', "", sdl)
            m = re.search(r'type\s+DAR_Adresse\s*\{([^}]*)\}', stripped)
            fields = []
            if m:
                for line in m.group(1).splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        fname = line.split(":")[0].split("(")[0].strip()
                        fields.append(fname)
            husnr_fields = [f for f in fields if re.search(r'husnummer', f, re.I)]
            print(f"   DAR_Adresse husnummer-ish fields: {husnr_fields}")
        except Exception as ex:
            print(f"   DAR schema fetch failed: {ex}")

    print(f"\n=== 4) BBR_Bygning field-population rate across Hørsholm (kommunekode 0223) ===")
    bbr_base = "https://graphql.datafordeler.dk/BBR/v3"
    q2 = f'''
    query {{
      BBR_Bygning(
        first: 200
        registreringstid: "{NOW}"
        virkningstid: "{NOW}"
        where: {{ kommunekode: {{ eq: "0223" }} }}
      ) {{
        nodes {{ husnummer byg026Opfoerelsesaar byg027OmTilbygningsaar byg032YdervaeggensMateriale byg033Tagdaekningsmateriale byg056Varmeinstallation byg057Opvarmningsmiddel byg070Fredning }}
      }}
    }}
    '''
    result = post_graphql(bbr_base, q2)
    try:
        nodes = result["data"]["BBR_Bygning"]["nodes"]
    except Exception:
        print("   query failed:", json.dumps(result)[:1000])
        return
    n = len(nodes)
    fields = ["byg026Opfoerelsesaar", "byg027OmTilbygningsaar", "byg032YdervaeggensMateriale",
              "byg033Tagdaekningsmateriale", "byg056Varmeinstallation", "byg057Opvarmningsmiddel",
              "byg070Fredning"]
    print(f"   sampled {n} buildings")
    for f in fields:
        filled = sum(1 for nd in nodes if nd.get(f) not in (None, ""))
        print(f"   {f}: {filled}/{n} filled ({filled*100//max(n,1)}%)")


if __name__ == "__main__":
    main()
