#!/usr/bin/env python3
"""Diagnostic-only: last checks before committing to the real region-wide
fetch for #26.

1. Cursor pagination on BBR_Bygning and EBR_Ejendomsbeliggenhed at a
   realistic page size (500) — does pageInfo/endCursor work as expected
   over multiple pages, any rate-limiting between calls?
2. Does DAR_Adresse support `where: { id_lokalId: { in: [...] } }` for
   batch resolution? ~90% of condo EBR records only give adresseLokalId
   (confirmed), so this needs to be batchable or the DAR resolution step
   turns into one query per address across tens of thousands of condos.
"""
import json
import os
import time
import urllib.error
import urllib.request

API_KEY = os.environ["DATAFORDELER_API"]
UA = {"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"}
NOW = "2026-08-03T12:00:00Z"


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
    bbr_base = "https://graphql.datafordeler.dk/BBR/v3"
    print("=== 1) BBR_Bygning cursor pagination, page size 500, kommunekode 0101 (København) ===", flush=True)
    cursor = None
    total = 0
    t0 = time.time()
    for page in range(3):  # just prove it works over a few pages, not the whole kommune
        after = f'after: "{cursor}"' if cursor else ""
        q = f'''
        query {{
          BBR_Bygning(
            first: 500 {after}
            registreringstid: "{NOW}"
            virkningstid: "{NOW}"
            where: {{ kommunekode: {{ eq: "0101" }} }}
          ) {{
            pageInfo {{ hasNextPage endCursor }}
            nodes {{ husnummer }}
          }}
        }}
        '''
        t1 = time.time()
        result = post_graphql(bbr_base, q)
        dt = time.time() - t1
        try:
            block = result["data"]["BBR_Bygning"]
            n = len(block["nodes"])
            total += n
            cursor = block["pageInfo"]["endCursor"]
            has_next = block["pageInfo"]["hasNextPage"]
            print(f"   page {page+1}: {n} nodes in {dt:.1f}s, hasNextPage={has_next}, cursor={cursor}")
            if not has_next:
                break
        except Exception:
            print(f"   page {page+1} FAILED: {json.dumps(result)[:500]}")
            break
    print(f"   total so far: {total} in {time.time()-t0:.1f}s")

    print("\n=== 2) EBR_Ejendomsbeliggenhed filtered + paginated by kommuneinddelingKommunekode ===")
    ebr_base = "https://graphql.datafordeler.dk/EBR/v1"
    q2 = f'''
    query {{
      EBR_Ejendomsbeliggenhed(
        first: 500
        registreringstid: "{NOW}"
        virkningstid: "{NOW}"
        where: {{ kommuneinddelingKommunekode: {{ eq: "0101" }} }}
      ) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{ bestemtFastEjendomBFENr husnummerLokalId adresseLokalId }}
      }}
    }}
    '''
    t1 = time.time()
    result2 = post_graphql(ebr_base, q2)
    dt = time.time() - t1
    try:
        block = result2["data"]["EBR_Ejendomsbeliggenhed"]
        print(f"   {len(block['nodes'])} nodes in {dt:.1f}s, hasNextPage={block['pageInfo']['hasNextPage']}")
    except Exception:
        print(f"   FAILED: {json.dumps(result2)[:500]}")

    print("\n=== 3) DAR_Adresse batch resolution via `in` operator ===")
    dar_base = "https://graphql.datafordeler.dk/DAR/v3"
    # grab a few real adresseLokalId values from the EBR page above to batch-test
    ids = []
    try:
        for nd in result2["data"]["EBR_Ejendomsbeliggenhed"]["nodes"]:
            if nd.get("adresseLokalId") and not nd.get("husnummerLokalId"):
                ids.append(nd["adresseLokalId"])
            if len(ids) >= 5:
                break
    except Exception:
        pass
    if not ids:
        print("   no adresseLokalId samples available from step 2, skipping")
        return
    id_list = ", ".join(f'"{i}"' for i in ids)
    q3 = f'''
    query {{
      DAR_Adresse(
        first: 10
        registreringstid: "{NOW}"
        virkningstid: "{NOW}"
        where: {{ id_lokalId: {{ in: [{id_list}] }} }}
      ) {{
        nodes {{ id_lokalId husnummer }}
      }}
    }}
    '''
    result3 = post_graphql(dar_base, q3)
    print(f"   requested {len(ids)} ids, result:")
    print(json.dumps(result3, ensure_ascii=False, indent=2)[:2000])


if __name__ == "__main__":
    main()
