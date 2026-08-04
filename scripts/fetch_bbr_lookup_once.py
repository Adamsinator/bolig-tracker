#!/usr/bin/env python3
"""One-off: build data/bbr_lookup.json for the address-lookup page
(model.html) — husnummer-keyed building attributes and
adresseIdentificerer-keyed unit area, for looking up a LIVE-PICKED address
(DAWA autocomplete), not a boligsiden listing.

Separate from data/bbr.json (#26), which is BFE-keyed for annotating
listings at build time — different contexts need different join keys.
Confirmed live before writing this: DAWA's picked-address IDs
(adresse.id, adgangsadresseid) are the exact same UUID space as DAR's
(id_lokalId / husnummer) — a real DAWA pick's adresse.id matched
DAR_Adresse.id_lokalId exactly, and that record's husnummer matched the
same pick's adgangsadresseid exactly. So no EBR/BFE resolution hop is
needed here at all, unlike #26 — a picked address's own IDs are the join
keys directly.

Real scale, confirmed live before writing this: BBR_Enhed's kommunekode
filter works identically to BBR_Bygning's; København alone has more than
3,000 records (didn't finish within a 6-page/3000-record probe cap) at
~1.1-2.9s per 500-record page — comparable to or faster than
BBR_Bygning's per-page latency during the real #26 fetch. There is no
confirmed total record count yet; this fetch's own per-kommune progress
logging is the real source of truth for total runtime, not a guess made
here. Staged and checkpointed exactly like fetch_bbr_once.py so a
slow/failing stage never discards already-fetched kommuner.
"""
import base64
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_KEY = os.environ["DATAFORDELER_API"]
UA = {"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"}
BBR = "https://graphql.datafordeler.dk/BBR/v3"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "..", "data")
OUT = os.path.join(DATA_DIR, "bbr_lookup.json")
CACHE_BUILDINGS = os.path.join(DATA_DIR, "bbr_lookup_cache_buildings.json")
CACHE_ENHEDER = os.path.join(DATA_DIR, "bbr_lookup_cache_enheder.json")
PAGE = 500

# Region Hovedstaden, Bornholm excluded — same 28 codes used throughout
# (grundareal.json, data/bbr.json, build_data.py's MUNICIPALITIES).
KOMMUNER = [
    "0101", "0147", "0157", "0173", "0230", "0159", "0190", "0201", "0219", "0223",
    "0151", "0163", "0240", "0210", "0165", "0153", "0155", "0250", "0161", "0270",
    "0260", "0217", "0167", "0169", "0183", "0175", "0185", "0187",
]

BBR_FIELDS = ("husnummer byg026Opfoerelsesaar byg027OmTilbygningsaar "
              "byg032YdervaeggensMateriale byg033Tagdaekningsmateriale "
              "byg056Varmeinstallation byg057Opvarmningsmiddel")
ENHED_FIELDS = "id_lokalId adresseIdentificerer enh026EnhedensSamledeAreal"


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
            # log the response body, not just str(ex) — the exact lesson
            # from #26's DAR_Adresse chunk-size bug, where the real cause
            # was invisible for 3+ hours because only the status line was
            # ever logged
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


def fetch_buildings_by_husnummer():
    """husnummer (UUID str) -> [year, renYear, wallCode, roofCode, heatCode,
    fuelCode], across all 28 kommuner. Kept husnummer-keyed — unlike #26,
    no BFE join needed, since a live address pick already gives husnummer
    directly (adgangsadresseid == DAR_Husnummer.id_lokalId, confirmed)."""
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
                rec = [
                    nd.get("byg026Opfoerelsesaar") or 0,
                    nd.get("byg027OmTilbygningsaar") or 0,
                    _code(nd.get("byg032YdervaeggensMateriale")),
                    _code(nd.get("byg033Tagdaekningsmateriale")),
                    _code(nd.get("byg056Varmeinstallation")),
                    _code(nd.get("byg057Opvarmningsmiddel")),
                ]
                # a husnummer can have >1 building (garage, shed…); keep
                # whichever record has more real data
                prev = buildings.get(hn)
                if prev is None or sum(1 for v in rec if v) > sum(1 for v in prev if v):
                    buildings[hn] = rec
                n_kommune += 1
            if not block["pageInfo"]["hasNextPage"]:
                break
            cursor = block["pageInfo"]["endCursor"]
        print(f"  BBR_Bygning {code}: {n_kommune} buildings ({len(buildings)} unique husnummer so far)", flush=True)
    return buildings


def fetch_enheder_by_adresse():
    """adresseIdentificerer (UUID str) -> area (m², int), across all 28 kommuner."""
    enheder = {}
    now = now_iso()
    for code in KOMMUNER:
        cursor, n_kommune = None, 0
        while True:
            after = f'after: "{cursor}"' if cursor else ""
            q = (f'query {{ BBR_Enhed(first: {PAGE} {after} registreringstid: "{now}" '
                 f'virkningstid: "{now}" where: {{ kommunekode: {{ eq: "{code}" }} }}) '
                 f'{{ pageInfo {{ hasNextPage endCursor }} nodes {{ {ENHED_FIELDS} }} }} }}')
            data = post_graphql(BBR, q)
            if data is None:
                break
            block = data["data"]["BBR_Enhed"]
            for nd in block["nodes"]:
                addr = nd.get("adresseIdentificerer")
                area = nd.get("enh026EnhedensSamledeAreal")
                if not addr or not area:
                    continue
                # a handful of registration slices can repeat the same unit;
                # keep the largest reported area
                prev = enheder.get(addr)
                if prev is None or area > prev:
                    enheder[addr] = area
                n_kommune += 1
            if not block["pageInfo"]["hasNextPage"]:
                break
            cursor = block["pageInfo"]["endCursor"]
        print(f"  BBR_Enhed {code}: {n_kommune} units ({len(enheder)} unique address so far)", flush=True)
    return enheder


def stage_buildings():
    if os.path.exists(CACHE_BUILDINGS):
        print("buildings cache already present — skipping fetch", flush=True)
        return
    t0 = time.time()
    print("fetching BBR_Bygning across 28 kommuner (husnummer-keyed)…", flush=True)
    buildings = fetch_buildings_by_husnummer()
    cache_write(CACHE_BUILDINGS, list(buildings.items()))
    print(f"wrote {CACHE_BUILDINGS} — {len(buildings)} unique husnummer, "
          f"{(time.time()-t0)/60:.1f} min", flush=True)


def stage_enheder():
    if os.path.exists(CACHE_ENHEDER):
        print("enheder cache already present — skipping fetch", flush=True)
        return
    t0 = time.time()
    print("fetching BBR_Enhed across 28 kommuner (adresseIdentificerer-keyed)…", flush=True)
    enheder = fetch_enheder_by_adresse()
    cache_write(CACHE_ENHEDER, list(enheder.items()))
    print(f"wrote {CACHE_ENHEDER} — {len(enheder)} unique addresses, "
          f"{(time.time()-t0)/60:.1f} min", flush=True)


def build_output(buildings_items, enheder_items):
    """Pulled out of the write stage so it's unit-testable against
    synthetic data without touching the network or the cache files."""
    b_flat = []
    for hn, rec in buildings_items:
        b_flat.append(hn)
        b_flat.extend(rec)
    e_flat = []
    for addr, area in enheder_items:
        e_flat.append(addr)
        e_flat.append(area)
    return b_flat, e_flat


def stage_write():
    buildings = cache_read(CACHE_BUILDINGS)
    enheder = cache_read(CACHE_ENHEDER)
    if buildings is None or enheder is None:
        sys.exit("buildings/enheder cache missing — run those stages first")

    b_flat, e_flat = build_output(buildings, enheder)
    if not b_flat and not e_flat:
        sys.exit("nothing to write — refusing to write an empty file")

    b_blob = base64.b64encode(gzip.compress(json.dumps(b_flat, separators=(",", ":")).encode(), 9)).decode()
    e_blob = base64.b64encode(gzip.compress(json.dumps(e_flat, separators=(",", ":")).encode(), 9)).decode()
    out = {
        "source": "BBR/v3 via Datafordeler GraphQL — live address-pick lookups (#26 follow-up)",
        "note": "buildings: flat [husnummer,year,renYear,wallCode,roofCode,heatCode,fuelCode]*N array. "
                "units: flat [adresseIdentificerer,areaM2]*N array. Both keyed by the exact UUIDs a "
                "DAWA adresser/autocomplete pick returns (adgangsadresseid for buildings, adresse.id "
                "for units) — confirmed live to match DAR's id_lokalId/husnummer exactly, so no BFE/EBR "
                "resolution is needed here (unlike data/bbr.json, which is BFE-keyed for listings). "
                "0 means missing/not recorded; wall/roof/heat/fuel are raw BBR kodeliste codes.",
        "buildingsCount": len(buildings),
        "unitsCount": len(enheder),
        "enc": "gzip+base64",
        "buildings": b_blob,
        "units": e_blob,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    mb = os.path.getsize(OUT) / 1e6
    print(f"wrote data/bbr_lookup.json — {len(buildings)} buildings, {len(enheder)} units, {mb:.2f} MB")
    if mb > 45:
        sys.exit("too large to commit")


STAGES = {
    "buildings": stage_buildings,
    "enheder": stage_enheder,
    "write": stage_write,
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
