#!/usr/bin/env python3
"""Diagnostic-only: the villa join test (bfeNumbers -> EBR) landed
husnummerLokalId populated directly. Condos are the other ~half of
listings and the earlier unfiltered EBR sample showed adresseLokalId
populated with husnummerLokalId null for at least some records — need to
know if that's the condo case, and if so what resolves adresseLokalId to
the husnummer-level ID BBR_Bygning.husnummer matches on."""
import json
import re
import os
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
    ebr_base = "https://graphql.datafordeler.dk/EBR/v1"
    print("=== pull several real condo listings, check EBR join field for each ===", flush=True)
    qs = urllib.parse.urlencode({
        "addressTypes": "condo", "municipalities": "koebenhavn", "per_page": 10, "page": 1,
    })
    with urllib.request.urlopen(urllib.request.Request(
            f"https://api.boligsiden.dk/search/cases?{qs}", headers=UA), timeout=60) as r:
        cases = json.load(r)["cases"]

    resolved_husnr, only_addr, neither = 0, 0, 0
    sample_addr_lokal_id = None
    for case in cases:
        bfe = (case.get("address") or {}).get("bfeNumbers")
        if not bfe:
            continue
        bfe_val = bfe[0] if isinstance(bfe, list) else bfe
        q = f'''
        query {{
          EBR_Ejendomsbeliggenhed(
            first: 3
            registreringstid: "{NOW}"
            virkningstid: "{NOW}"
            where: {{ bestemtFastEjendomBFENr: {{ eq: "{bfe_val}" }} }}
          ) {{
            nodes {{ bestemtFastEjendomBFENr adresseLokalId husnummerLokalId }}
          }}
        }}
        '''
        result = post_graphql(ebr_base, q)
        try:
            nodes = result["data"]["EBR_Ejendomsbeliggenhed"]["nodes"]
        except Exception:
            print(f"  bfe={bfe_val}: query failed {json.dumps(result)[:200]}")
            continue
        if not nodes:
            print(f"  bfe={bfe_val}: no EBR match")
            continue
        nd = nodes[0]
        hn, ad = nd.get("husnummerLokalId"), nd.get("adresseLokalId")
        print(f"  bfe={bfe_val}: husnummerLokalId={hn!r} adresseLokalId={ad!r}")
        if hn:
            resolved_husnr += 1
        elif ad:
            only_addr += 1
            sample_addr_lokal_id = sample_addr_lokal_id or ad
        else:
            neither += 1

    print(f"\nsummary: husnummerLokalId directly usable={resolved_husnr}, "
          f"only adresseLokalId={only_addr}, neither={neither}")

    if sample_addr_lokal_id:
        print(f"\n=== DAR_Adresse schema — resolving adresseLokalId={sample_addr_lokal_id} ===")
        dar_base = "https://graphql.datafordeler.dk/DAR/v3"
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
        print(f"  DAR_Adresse husnummer-ish fields: {husnr_fields}")
        print(f"  all DAR_Adresse fields: {fields}")

        if husnr_fields:
            q2 = f'''
            query {{
              DAR_Adresse(
                first: 1
                registreringstid: "{NOW}"
                virkningstid: "{NOW}"
                where: {{ id_lokalId: {{ eq: "{sample_addr_lokal_id}" }} }}
              ) {{
                nodes {{ id_lokalId {" ".join(husnr_fields)} }}
              }}
            }}
            '''
            print(f"\n  live query resolving that address -> husnummer:")
            print(json.dumps(post_graphql(dar_base, q2), ensure_ascii=False, indent=2)[:1500])


if __name__ == "__main__":
    main()
