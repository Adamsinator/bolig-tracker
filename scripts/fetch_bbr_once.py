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

Staged and checkpointed (#26 postmortem): the first real run fetched
BBR (381,141 buildings, 125 min) and EBR (592,769 properties, 25 min)
cleanly, then hit a hardcoded DAR_Adresse batch size of 200 — the
real server-side limit is 100 (confirmed live: "The number of
elements in the supplied 'in' list is not allowed to exceed 100",
code DAF-GQL-0016) — so every single DAR request failed for the
remaining 3h10m until the workflow's timeout killed it. Because the
old script only wrote output at the very end, all of that good
BBR+EBR data was lost with it.

Now each stage (buildings / ebr / resolve / join) is invoked
separately (`python3 fetch_bbr_once.py <stage>`) and caches its
result to its own data/bbr_cache_*.json, which the workflow commits
immediately after each stage. A stage whose cache already exists on
disk skips its own work — so re-dispatching after a failure resumes
instead of re-paying for already-fetched data.
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
DATA_DIR = os.path.join(HERE, "..", "data")
OUT = os.path.join(DATA_DIR, "bbr.json")
CACHE_BUILDINGS = os.path.join(DATA_DIR, "bbr_cache_buildings.json")
CACHE_EBR = os.path.join(DATA_DIR, "bbr_cache_ebr.json")
CACHE_DAR = os.path.join(DATA_DIR, "bbr_cache_dar.json")
PAGE = 500
# DAR_Adresse's real server-side cap on an 'in' filter list — confirmed live
# (probe_dar_batch.py): 100 works, 150+ all fail with DAF-GQL-0016. The
# original 200 guessed too high and was never checked at scale.
DAR_CHUNK = 100

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
        except urllib.error.HTTPError as ex:
            # the response body (never the request URL, which carries apiKey)
            # is where the actual GraphQL/HTTP validation reason lives — an
            # HTTPError's str() alone is just the status line and hid the
            # real cause of a run that burned 3+ hours retrying a 400
            detail = ""
            try:
                detail = ex.read().decode("utf-8", "replace")[:500]
            except Exception:
                pass
            if i == tries - 1:
                print(f"    request failed after {tries} tries: {ex} — {detail}", file=sys.stderr)
                return None
            time.sleep(2 * (i + 1))
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


def cache_write(path, obj):
    blob = base64.b64encode(gzip.compress(json.dumps(obj, separators=(",", ":")).encode(), 9)).decode()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"data": blob}, fh, separators=(",", ":"))


def cache_read(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    return json.loads(gzip.decompress(base64.b64decode(doc["data"])))


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
    """Batch-resolve DAR_Adresse.id_lokalId -> husnummer, chunked at the
    server's real limit (DAR_CHUNK = 100, confirmed live)."""
    resolved = {}
    now = now_iso()
    ids = sorted(addr_ids)
    t0 = time.time()
    for i in range(0, len(ids), DAR_CHUNK):
        chunk = ids[i:i + DAR_CHUNK]
        id_list = ", ".join(f'"{x}"' for x in chunk)
        q = (f'query {{ DAR_Adresse(first: {DAR_CHUNK} registreringstid: "{now}" '
             f'virkningstid: "{now}" where: {{ id_lokalId: {{ in: [{id_list}] }} }}) '
             f'{{ nodes {{ id_lokalId husnummer }} }} }}')
        data = post_graphql(DAR, q)
        if data is None:
            continue
        for nd in data["data"]["DAR_Adresse"]["nodes"]:
            if nd.get("husnummer"):
                resolved[nd["id_lokalId"]] = nd["husnummer"]
        if (i // DAR_CHUNK) % 10 == 0:
            print(f"    DAR resolved {len(resolved)}/{len(ids)}, "
                  f"{(time.time()-t0)/60:.1f} min so far", flush=True)
    return resolved


def build_output(buildings, bfe_map, dar_resolved):
    """Join BFE -> husnummer -> building into the flat encode-ready array.
    Pulled out of the join stage so it's unit-testable against synthetic
    dicts without touching the network or the cache files."""
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
    return flat, matched


def stage_buildings():
    if os.path.exists(CACHE_BUILDINGS):
        print("buildings cache already present — skipping fetch", flush=True)
        return
    t0 = time.time()
    print("fetching BBR_Bygning across 28 kommuner…", flush=True)
    buildings = fetch_bbr_buildings()
    cache_write(CACHE_BUILDINGS, buildings)
    print(f"wrote {CACHE_BUILDINGS} — {len(buildings)} unique husnummer, "
          f"{(time.time()-t0)/60:.1f} min", flush=True)


def stage_ebr():
    if os.path.exists(CACHE_EBR):
        print("EBR cache already present — skipping fetch", flush=True)
        return
    t0 = time.time()
    print("fetching EBR_Ejendomsbeliggenhed across 28 kommuner…", flush=True)
    bfe_map = fetch_bfe_to_husnummer()
    cache_write(CACHE_EBR, list(bfe_map.items()))
    print(f"wrote {CACHE_EBR} — {len(bfe_map)} BFE numbers, "
          f"{(time.time()-t0)/60:.1f} min", flush=True)


def stage_resolve():
    if os.path.exists(CACHE_DAR):
        print("DAR cache already present — skipping resolution", flush=True)
        return
    bfe_items = cache_read(CACHE_EBR)
    if bfe_items is None:
        sys.exit(f"{CACHE_EBR} missing — run the 'ebr' stage first")
    needs_dar = {addr for _, (hn, addr) in bfe_items if not hn and addr}
    t0 = time.time()
    print(f"resolving {len(needs_dar)} adresseLokalId -> husnummer via DAR_Adresse…", flush=True)
    dar_resolved = resolve_adresse_to_husnummer(needs_dar) if needs_dar else {}
    cache_write(CACHE_DAR, list(dar_resolved.items()))
    print(f"wrote {CACHE_DAR} — resolved {len(dar_resolved)}/{len(needs_dar)}, "
          f"{(time.time()-t0)/60:.1f} min", flush=True)


def stage_join():
    buildings = cache_read(CACHE_BUILDINGS)
    bfe_items = cache_read(CACHE_EBR)
    if buildings is None or bfe_items is None:
        sys.exit("buildings/EBR cache missing — run those stages first")
    bfe_map = {bfe: tuple(v) for bfe, v in bfe_items}
    dar_items = cache_read(CACHE_DAR) or []
    dar_resolved = dict(dar_items)

    print("joining BFE -> husnummer -> building…", flush=True)
    flat, matched = build_output(buildings, bfe_map, dar_resolved)
    print(f"  {matched}/{len(bfe_map)} BFE numbers matched to a building with real data")
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
    print(f"wrote data/bbr.json — {matched} properties, {mb:.2f} MB")
    if mb > 45:
        sys.exit("too large to commit")


STAGES = {
    "buildings": stage_buildings,
    "ebr": stage_ebr,
    "resolve": stage_resolve,
    "join": stage_join,
}


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    if stage == "all":
        for fn in STAGES.values():
            fn()
        return
    if stage not in STAGES:
        sys.exit(f"unknown stage {stage!r} — choose one of {list(STAGES)} or 'all'")
    STAGES[stage]()


if __name__ == "__main__":
    main()
