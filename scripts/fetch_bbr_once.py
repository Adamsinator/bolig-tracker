#!/usr/bin/env python3
"""One-off: pull BBR building attributes (year built, renovation year, wall
material, roof material, heating installation, heating fuel) for every
property in Region Hovedstaden and write data/bbr.json, keyed by BFE number
(#26).

Why BFE number: boligsiden's listing payload carries `address.bfeNumbers`
directly (confirmed by probing a real listing), and Boliga's sold records
carry `bfEnr` too — so BFE is the one join key both the daily build and a
future address-lookup feature can use without re-deriving anything.

The join, confirmed live against real data before this was written:
  BFE number
    -> EBR_Ejendomsbeliggenhed (Datafordeler), filtered by
       bestemtFastEjendomBFENr
    -> either husnummerLokalId directly (villas: ~confirmed 1-in-10 in a
       condo sample, presumably closer to all for villas), or
       adresseLokalId (condos: ~9-in-10 in the same sample)
    -> if adresseLokalId: DAR_Adresse.husnummer resolves it (batched via
       `where: { id_lokalId: { in: [...] } }`, confirmed working)
    -> BBR_Bygning.husnummer matches directly, giving the building record

Real field-population rates from a 200-building Hørsholm sample (not
guessed): byg026Opfoerelsesaar (year) 84%, byg032/033 (wall/roof material)
74% each, byg056Varmeinstallation (heating) 42%, byg057Opvarmningsmiddel
(heating fuel) 31%, byg027OmTilbygningsaar (renovation year) 13% — sparse
but real when present.

Runs once: BFE/BBR/DAR barely change day to day, same shape as
grundareal.json (matriklen) and noise.json (Miljøstyrelsen).
"""
import base64
import gzip
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

API_KEY = os.environ["DATAFORDELER_API"]
UA = {"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"}
BBR = "https://graphql.datafordeler.dk/BBR/v3"
EBR = "https://graphql.datafordeler.dk/EBR/v1"
DAR = "https://graphql.datafordeler.dk/DAR/v3"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "data", "bbr.json")
PAGE = 500

# Region Hovedstaden, Bornholm excluded — same 28 codes used throughout
# (grundareal.json, build_data.py's MUNICIPALITIES).
KOMMUNER = [
    "0101", "0147", "0157", "0173", "0230", "0159", "0190", "0201", "0219", "0223",
    "0151", "0163", "0240", "0210", "0165", "0153", "0155", "0250", "0161", "0270",
    "0260", "0217", "0167", "0169", "0183", "0175", "0185", "0187",
]

BBR_FIELDS = ("husnummer byg026Opfoerelsesaar byg027OmTilbygningsaar "
              "byg032YdervaeggensMateriale byg033Tagdaekningsmateriale "
              "byg056Varmeinstallation byg057Opvarmningsmiddel")


def post_graphql(base, query, tries=5):
    body = json.dumps({"query": query}).encode("utf-8")
    for i in range(tries):
        try:
            req = urllib.request.Request(f"{base}?apiKey={API_KEY}", data=body,
                                          headers={**UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read())
            if "errors" in data:
                print(f"    graphql errors: {json.dumps(data['errors'])[:300]}", file=sys.stderr)
                return None
            return data
        except Exception as ex:
            if i == tries - 1:
                print(f"    request failed after {tries} tries: {ex}", file=sys.stderr)
                return None
            time.sleep(2 * (i + 1))
    return None


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _code(v):
    """BBR kodeliste values come back as numeric strings ('80', '11'). Store
    as int; 0 means missing/unparseable, which is never a real code."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def fetch_bbr_buildings():
    """husnummer (UUID str) -> dict of building fields, across all 28 kommuner."""
    buildings = {}
    now = now_iso()
    for code in KOMMUNER:
        cursor, n_kommune = None, 0
        while True:
            after = f'after: "{cursor}"' if cursor else ""
            q = (f'query {{ BBR_Bygning(first: {PAGE} {after} registreringstid: "{now}" '
                 f'virkningstid: "{now}" where: {{ kommunekode: {{ eq: "{code}" }} }}) '
                 f'{{ pageInfo {{ hasNextPage endCursor }} nodes {{ {BBR_FIELDS} }} }} }}')
            data = post_graphql(BBR, q)
            if data is None:
                break
            block = data["data"]["BBR_Bygning"]
            for nd in block["nodes"]:
                hn = nd.get("husnummer")
                if not hn:
                    continue
                rec = {
                    "y": nd.get("byg026Opfoerelsesaar") or 0,
                    "ren": nd.get("byg027OmTilbygningsaar") or 0,
                    "wall": _code(nd.get("byg032YdervaeggensMateriale")),
                    "roof": _code(nd.get("byg033Tagdaekningsmateriale")),
                    "heat": _code(nd.get("byg056Varmeinstallation")),
                    "fuel": _code(nd.get("byg057Opvarmningsmiddel")),
                }
                # a husnummer can have >1 building (garage, shed…); keep
                # whichever record has more real data
                prev = buildings.get(hn)
                if prev is None or sum(1 for v in rec.values() if v) > sum(1 for v in prev.values() if v):
                    buildings[hn] = rec
                n_kommune += 1
            if not block["pageInfo"]["hasNextPage"]:
                break
            cursor = block["pageInfo"]["endCursor"]
        print(f"  BBR {code}: {n_kommune} buildings ({len(buildings)} unique husnummer so far)", flush=True)
    return buildings


def fetch_bfe_to_husnummer():
    """BFE number (str) -> (husnummerLokalId, adresseLokalId), across all 28 kommuner."""
    bfe_map = {}
    now = now_iso()
    for code in KOMMUNER:
        cursor, n_kommune = None, 0
        while True:
            after = f'after: "{cursor}"' if cursor else ""
            q = (f'query {{ EBR_Ejendomsbeliggenhed(first: {PAGE} {after} registreringstid: "{now}" '
                 f'virkningstid: "{now}" where: {{ kommuneinddelingKommunekode: {{ eq: "{code}" }} }}) '
                 f'{{ pageInfo {{ hasNextPage endCursor }} '
                 f'nodes {{ bestemtFastEjendomBFENr husnummerLokalId adresseLokalId status }} }} }}')
            data = post_graphql(EBR, q)
            if data is None:
                break
            block = data["data"]["EBR_Ejendomsbeliggenhed"]
            for nd in block["nodes"]:
                if nd.get("status") != "gældende":
                    continue
                bfe = nd.get("bestemtFastEjendomBFENr")
                if not bfe:
                    continue
                bfe_map[bfe] = (nd.get("husnummerLokalId"), nd.get("adresseLokalId"))
                n_kommune += 1
            if not block["pageInfo"]["hasNextPage"]:
                break
            cursor = block["pageInfo"]["endCursor"]
        print(f"  EBR {code}: {n_kommune} properties ({len(bfe_map)} unique BFE so far)", flush=True)
    return bfe_map


def resolve_adresse_to_husnummer(addr_ids):
    """Batch-resolve DAR_Adresse.id_lokalId -> husnummer, chunked."""
    resolved = {}
    now = now_iso()
    ids = sorted(addr_ids)
    CHUNK = 200
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        id_list = ", ".join(f'"{x}"' for x in chunk)
        q = (f'query {{ DAR_Adresse(first: {CHUNK} registreringstid: "{now}" '
             f'virkningstid: "{now}" where: {{ id_lokalId: {{ in: [{id_list}] }} }}) '
             f'{{ nodes {{ id_lokalId husnummer }} }} }}')
        data = post_graphql(DAR, q)
        if data is None:
            continue
        for nd in data["data"]["DAR_Adresse"]["nodes"]:
            if nd.get("husnummer"):
                resolved[nd["id_lokalId"]] = nd["husnummer"]
        if (i // CHUNK) % 20 == 0:
            print(f"    DAR resolved {len(resolved)}/{len(ids)}", flush=True)
    return resolved


def main():
    t0 = time.time()
    print("1) fetching BBR_Bygning across 28 kommuner…", flush=True)
    buildings = fetch_bbr_buildings()
    print(f"   {len(buildings)} unique husnummer with building data, "
          f"{(time.time()-t0)/60:.1f} min so far\n")

    print("2) fetching EBR_Ejendomsbeliggenhed across 28 kommuner…", flush=True)
    bfe_map = fetch_bfe_to_husnummer()
    print(f"   {len(bfe_map)} BFE numbers, {(time.time()-t0)/60:.1f} min so far\n")

    needs_dar = {addr for hn, addr in bfe_map.values() if not hn and addr}
    print(f"3) resolving {len(needs_dar)} adresseLokalId -> husnummer via DAR_Adresse…", flush=True)
    dar_resolved = resolve_adresse_to_husnummer(needs_dar) if needs_dar else {}
    print(f"   resolved {len(dar_resolved)}/{len(needs_dar)}, {(time.time()-t0)/60:.1f} min so far\n")

    print("4) joining BFE -> husnummer -> building…", flush=True)
    flat = []
    matched = 0
    for bfe, (hn, addr) in bfe_map.items():
        husnr = hn or (dar_resolved.get(addr) if addr else None)
        if not husnr:
            continue
        rec = buildings.get(husnr)
        if not rec or not any(rec.values()):
            continue
        matched += 1
        flat.extend((int(bfe), rec["y"], rec["ren"], rec["wall"], rec["roof"], rec["heat"], rec["fuel"]))
    print(f"   {matched}/{len(bfe_map)} BFE numbers matched to a building with real data")

    if matched == 0:
        sys.exit("nothing matched — refusing to write an empty file")

    blob = base64.b64encode(gzip.compress(json.dumps(flat, separators=(",", ":")).encode(), 9)).decode()
    out = {
        "source": "BBR/v3 + EBR/v1 + DAR/v3 via Datafordeler GraphQL (#26)",
        "note": "flat [bfe,year,renYear,wallCode,roofCode,heatCode,fuelCode]*N array; "
                "0 means missing/not recorded. wall/roof/heat/fuel are raw BBR kodeliste "
                "codes, treated as categorical — not decoded to labels.",
        "count": matched,
        "enc": "gzip+base64",
        "data": blob,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    mb = os.path.getsize(OUT) / 1e6
    print(f"\nwrote data/bbr.json — {matched} properties, {mb:.2f} MB, "
          f"{(time.time()-t0)/60:.1f} min total")
    if mb > 45:
        sys.exit("too large to commit")


if __name__ == "__main__":
    main()
