#!/usr/bin/env python3
"""One-off: build data/dar_husnumre.json + data/dar_enheder.json — a self-hosted mirror of DAR
(Danmarks Adresseregister) for the 28 Region Hovedstaden kommuner, so the
live address-lookup features (setupGeo() in app.js, setupLookup() in
modelpage.js) never have to call DAWA or any other live address API again.
DAWA sunsets 17 Aug 2026; its official replacement (Adressevaelger) turned
out to have real gaps when tested live (no coordinates, no unit/floor
breakdown, and its token isn't actually validated today) — not something
to build a permanent migration on. This fetches the real register instead,
via the same DATAFORDELER_API key already used for BBR_Bygning/BBR_Enhed
in fetch_bbr_lookup_once.py.

Schema confirmed live before writing this (introspection is disabled on
this API, so every field below was verified field-by-field against real
data, not assumed):
  - DAR_Husnummer has no kommunekode field/filter (unlike BBR). The path
    in is DAR_NavngivenVejKommunedel, which filters by kommune (plain
    4-digit code) and links to navngivenVej (a street UUID); DAR_Husnummer
    then filters cleanly on `where: { navngivenVej: { in: [...] } }` —
    verified against 3 real Hørsholm street ids, got real Hørsholm
    addresses back.
  - DAR_Husnummer.adgangsadressebetegnelse is already a full pre-formatted
    address string ("Sofievej 11, 2970 Hørsholm") — no separate join to
    DAR_NavngivenVej or DAR_Postnummer is needed just for display/search
    text.
  - DAR_Husnummer.adgangspunkt is a plain string id, not a nested object —
    resolved via a separate DAR_Adressepunkt(where:{id_lokalId:{in:...}})
    query. Its `position` field is a SpatialPointEpsg25832Type; the only
    valid subfield is `wkt` (a "POINT (x y)" string in ETRS89/UTM zone 32N,
    NOT WGS84) — reprojected with the same _utm32n_to_wgs84() already
    proven in build_data.py's kommune-boundary fetch (#27).
  - DAR_Adresse.{id_lokalId, etagebetegnelse, doerbetegnelse, husnummer}
    all confirmed valid; id_lokalId is the exact same UUID DAWA's own
    adresse.id was, and BBR_Enhed.adresseIdentificerer too (confirmed
    live in the #26 follow-up) — so a picked unit here joins directly into
    the already-existing data/bbr_lookup.json with no extra resolution.

Staged and checkpointed like fetch_bbr_lookup_once.py, so a slow/failing
later stage never discards already-fetched data.
"""
import base64
import gzip
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request

API_KEY = os.environ["DATAFORDELER_API"]
UA = {"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"}
DAR = "https://graphql.datafordeler.dk/DAR/v3"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "..", "data")
OUT_HUSNUMRE = os.path.join(DATA_DIR, "dar_husnumre.json")
OUT_ENHEDER = os.path.join(DATA_DIR, "dar_enheder.json")
CACHE_VEJIDS = os.path.join(DATA_DIR, "dar_addresses_cache_vejids.json")
CACHE_HUSNUMMER = os.path.join(DATA_DIR, "dar_addresses_cache_husnummer.json")
CACHE_ADRESSEPUNKTER = os.path.join(DATA_DIR, "dar_addresses_cache_adressepunkter.json")
CACHE_ADRESSER = os.path.join(DATA_DIR, "dar_addresses_cache_adresser.json")
PAGE = 500
# conservative `in`-list chunk sizes — #26's DAR_Adresse fetch found 200
# silently failed where 100 worked; using 100 here too since it's the only
# proven-safe number on this API, and error bodies are logged (unlike #26)
# so a wrong guess fails fast and visibly instead of looping silently.
VEJ_CHUNK = 50
ID_CHUNK = 100

# Region Hovedstaden, Bornholm excluded — same 28 codes used throughout
# (grundareal.json, data/bbr.json, data/bbr_lookup.json, build_data.py's
# MUNICIPALITIES).
KOMMUNER = [
    "0101", "0147", "0157", "0173", "0230", "0159", "0190", "0201", "0219", "0223",
    "0151", "0163", "0240", "0210", "0165", "0153", "0155", "0250", "0161", "0270",
    "0260", "0217", "0167", "0169", "0183", "0175", "0185", "0187",
]


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def post_graphql(query, tries=5):
    body = json.dumps({"query": query}).encode("utf-8")
    for i in range(tries):
        try:
            req = urllib.request.Request(f"{DAR}?apiKey={API_KEY}", data=body,
                                          headers={**UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read())
            if "errors" in data:
                print(f"    graphql errors: {json.dumps(data['errors'])[:500]}", file=sys.stderr)
                return None
            return data
        except urllib.error.HTTPError as ex:
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


def chunked(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _utm32n_to_wgs84(easting, northing):
    """Identical to build_data.py's helper of the same name (#27) — kept
    self-contained here since every fetch_*_once.py script in this repo is
    standalone, not importing from build_data.py."""
    a = 6378137.0
    f = 1 / 298.257222101
    k0 = 0.9996
    E0 = 500000.0
    lon0 = math.radians(9.0)

    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)

    M = northing / k0
    mu = M / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))

    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu
            + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))

    C1 = ep2 * math.cos(phi1) ** 2
    T1 = math.tan(phi1) ** 2
    N1 = a / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    R1 = a * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    D = (easting - E0) / (N1 * k0)

    lat = phi1 - (N1 * math.tan(phi1) / R1) * (
        D ** 2 / 2
        - (5 + 3 * T1 + 10 * C1 - 4 * C1 ** 2 - 9 * ep2) * D ** 4 / 24
        + (61 + 90 * T1 + 298 * C1 + 45 * T1 ** 2 - 252 * ep2 - 3 * C1 ** 2) * D ** 6 / 720
    )
    lon = lon0 + (
        D - (1 + 2 * T1 + C1) * D ** 3 / 6
        + (5 - 2 * C1 + 28 * T1 - 3 * C1 ** 2 + 8 * ep2 + 24 * T1 ** 2) * D ** 5 / 120
    ) / math.cos(phi1)

    return math.degrees(lat), math.degrees(lon)


_WKT_POINT_RE = re.compile(r"POINT\s*\(\s*([\-0-9.]+)\s+([\-0-9.]+)\s*\)", re.IGNORECASE)


def parse_wkt_point(wkt):
    """'POINT (723930 6179643.37)' (EPSG:25832) -> (easting, northing) floats."""
    m = _WKT_POINT_RE.match(wkt or "")
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def fetch_vejids_by_kommune():
    """Union of navngivenVej UUIDs across all 28 kommuner, via
    DAR_NavngivenVejKommunedel — the only entity that filters by kommune
    directly (DAR_Husnummer/DAR_Adresse don't)."""
    vejids = set()
    now = now_iso()
    for code in KOMMUNER:
        cursor, n_kommune = None, 0
        while True:
            after = f'after: "{cursor}"' if cursor else ""
            q = (f'query {{ DAR_NavngivenVejKommunedel(first: {PAGE} {after} '
                 f'registreringstid: "{now}" virkningstid: "{now}" '
                 f'where: {{ kommune: {{ eq: "{code}" }} }}) '
                 f'{{ pageInfo {{ hasNextPage endCursor }} nodes {{ navngivenVej }} }} }}')
            data = post_graphql(q)
            if data is None:
                break
            block = data["data"]["DAR_NavngivenVejKommunedel"]
            for nd in block["nodes"]:
                v = nd.get("navngivenVej")
                if v:
                    vejids.add(v)
                    n_kommune += 1
            if not block["pageInfo"]["hasNextPage"]:
                break
            cursor = block["pageInfo"]["endCursor"]
        print(f"  kommunedel {code}: {n_kommune} street links ({len(vejids)} unique streets so far)", flush=True)
    return sorted(vejids)


def fetch_husnummer_by_vejids(vejids):
    """id_lokalId -> [adgangsadressebetegnelse, adgangspunkt] for every
    house number on any of the given streets."""
    husnumre = {}
    now = now_iso()
    for i, chunk in enumerate(chunked(vejids, VEJ_CHUNK)):
        vej_list = ", ".join(f'"{v}"' for v in chunk)
        cursor, n_chunk = None, 0
        while True:
            after = f'after: "{cursor}"' if cursor else ""
            q = (f'query {{ DAR_Husnummer(first: {PAGE} {after} '
                 f'registreringstid: "{now}" virkningstid: "{now}" '
                 f'where: {{ navngivenVej: {{ in: [{vej_list}] }} }}) '
                 f'{{ pageInfo {{ hasNextPage endCursor }} '
                 f'nodes {{ id_lokalId adgangsadressebetegnelse adgangspunkt }} }} }}')
            data = post_graphql(q)
            if data is None:
                break
            block = data["data"]["DAR_Husnummer"]
            for nd in block["nodes"]:
                hid = nd.get("id_lokalId")
                text = nd.get("adgangsadressebetegnelse")
                ap = nd.get("adgangspunkt")
                if not hid or not text:
                    continue
                husnumre[hid] = [text, ap]
                n_chunk += 1
            if not block["pageInfo"]["hasNextPage"]:
                break
            cursor = block["pageInfo"]["endCursor"]
        print(f"  vej-chunk {i + 1}/{-(-len(vejids) // VEJ_CHUNK)}: "
              f"{n_chunk} husnumre ({len(husnumre)} total so far)", flush=True)
    return husnumre


def fetch_adressepunkter(ap_ids):
    """id_lokalId -> [lat, lon] for every access-point id in ap_ids."""
    points = {}
    now = now_iso()
    ap_ids = sorted(ap_ids)
    for i, chunk in enumerate(chunked(ap_ids, ID_CHUNK)):
        id_list = ", ".join(f'"{v}"' for v in chunk)
        cursor = None
        while True:
            after = f'after: "{cursor}"' if cursor else ""
            q = (f'query {{ DAR_Adressepunkt(first: {PAGE} {after} '
                 f'registreringstid: "{now}" virkningstid: "{now}" '
                 f'where: {{ id_lokalId: {{ in: [{id_list}] }} }}) '
                 f'{{ pageInfo {{ hasNextPage endCursor }} '
                 f'nodes {{ id_lokalId position {{ wkt }} }} }} }}')
            data = post_graphql(q)
            if data is None:
                break
            block = data["data"]["DAR_Adressepunkt"]
            for nd in block["nodes"]:
                pid = nd.get("id_lokalId")
                pos = nd.get("position") or {}
                xy = parse_wkt_point(pos.get("wkt"))
                if not pid or not xy:
                    continue
                lat, lon = _utm32n_to_wgs84(xy[0], xy[1])
                points[pid] = [round(lat, 6), round(lon, 6)]
            if not block["pageInfo"]["hasNextPage"]:
                break
            cursor = block["pageInfo"]["endCursor"]
        if (i + 1) % 20 == 0 or i == 0:
            print(f"  adressepunkt-chunk {i + 1}/{-(-len(ap_ids) // ID_CHUNK)}: "
                  f"{len(points)} resolved so far", flush=True)
    return points


def fetch_adresser_by_husnummer(husnummer_ids):
    """[id_lokalId, husnummer, etagebetegnelse, doerbetegnelse] for every
    unit (DAR_Adresse) attached to any of the given house numbers."""
    adresser = []
    now = now_iso()
    husnummer_ids = sorted(husnummer_ids)
    for i, chunk in enumerate(chunked(husnummer_ids, ID_CHUNK)):
        id_list = ", ".join(f'"{v}"' for v in chunk)
        cursor, n_chunk = None, 0
        while True:
            after = f'after: "{cursor}"' if cursor else ""
            q = (f'query {{ DAR_Adresse(first: {PAGE} {after} '
                 f'registreringstid: "{now}" virkningstid: "{now}" '
                 f'where: {{ husnummer: {{ in: [{id_list}] }} }}) '
                 f'{{ pageInfo {{ hasNextPage endCursor }} '
                 f'nodes {{ id_lokalId husnummer etagebetegnelse doerbetegnelse }} }} }}')
            data = post_graphql(q)
            if data is None:
                break
            block = data["data"]["DAR_Adresse"]
            for nd in block["nodes"]:
                aid = nd.get("id_lokalId")
                hn = nd.get("husnummer")
                if not aid or not hn:
                    continue
                adresser.append([aid, hn, nd.get("etagebetegnelse") or "", nd.get("doerbetegnelse") or ""])
                n_chunk += 1
            if not block["pageInfo"]["hasNextPage"]:
                break
            cursor = block["pageInfo"]["endCursor"]
        if (i + 1) % 20 == 0 or i == 0:
            print(f"  husnummer-chunk {i + 1}/{-(-len(husnummer_ids) // ID_CHUNK)}: "
                  f"{n_chunk} units ({len(adresser)} total so far)", flush=True)
    return adresser


def stage_vejids():
    if os.path.exists(CACHE_VEJIDS):
        print("vejids cache already present — skipping fetch", flush=True)
        return
    t0 = time.time()
    print("fetching DAR_NavngivenVejKommunedel across 28 kommuner…", flush=True)
    vejids = fetch_vejids_by_kommune()
    cache_write(CACHE_VEJIDS, vejids)
    print(f"wrote {CACHE_VEJIDS} — {len(vejids)} unique streets, "
          f"{(time.time()-t0)/60:.1f} min", flush=True)


def stage_husnummer():
    if os.path.exists(CACHE_HUSNUMMER):
        print("husnummer cache already present — skipping fetch", flush=True)
        return
    vejids = cache_read(CACHE_VEJIDS)
    if vejids is None:
        sys.exit("vejids cache missing — run the vejids stage first")
    t0 = time.time()
    print(f"fetching DAR_Husnummer for {len(vejids)} streets…", flush=True)
    husnumre = fetch_husnummer_by_vejids(vejids)
    cache_write(CACHE_HUSNUMMER, list(husnumre.items()))
    print(f"wrote {CACHE_HUSNUMMER} — {len(husnumre)} husnumre, "
          f"{(time.time()-t0)/60:.1f} min", flush=True)


def stage_adressepunkter():
    if os.path.exists(CACHE_ADRESSEPUNKTER):
        print("adressepunkter cache already present — skipping fetch", flush=True)
        return
    husnumre = cache_read(CACHE_HUSNUMMER)
    if husnumre is None:
        sys.exit("husnummer cache missing — run the husnummer stage first")
    ap_ids = {ap for _, (_, ap) in husnumre if ap}
    t0 = time.time()
    print(f"fetching DAR_Adressepunkt for {len(ap_ids)} access points…", flush=True)
    points = fetch_adressepunkter(ap_ids)
    cache_write(CACHE_ADRESSEPUNKTER, list(points.items()))
    print(f"wrote {CACHE_ADRESSEPUNKTER} — {len(points)} resolved coordinates, "
          f"{(time.time()-t0)/60:.1f} min", flush=True)


def stage_adresser():
    if os.path.exists(CACHE_ADRESSER):
        print("adresser cache already present — skipping fetch", flush=True)
        return
    husnumre = cache_read(CACHE_HUSNUMMER)
    if husnumre is None:
        sys.exit("husnummer cache missing — run the husnummer stage first")
    husnummer_ids = [hid for hid, _ in husnumre]
    t0 = time.time()
    print(f"fetching DAR_Adresse (units) for {len(husnummer_ids)} husnumre…", flush=True)
    adresser = fetch_adresser_by_husnummer(husnummer_ids)
    cache_write(CACHE_ADRESSER, adresser)
    print(f"wrote {CACHE_ADRESSER} — {len(adresser)} units, "
          f"{(time.time()-t0)/60:.1f} min", flush=True)


def build_output(husnumre_items, points_items, adresser):
    """Pulled out of the write stage so it's unit-testable against
    synthetic data without touching the network or the cache files.
    husnumre_items: [(id_lokalId, [text, adgangspunkt]), ...]
    points_items: [(adgangspunkt_id, [lat, lon]), ...]
    adresser: [[id_lokalId, husnummer, etage, doer], ...]
    """
    points = dict(points_items)
    h_flat = []
    resolved = 0
    for hid, (text, ap) in husnumre_items:
        latlon = points.get(ap) if ap else None
        if not latlon:
            continue
        h_flat.append(hid)
        h_flat.append(text)
        h_flat.append(latlon[0])
        h_flat.append(latlon[1])
        resolved += 1
    a_flat = []
    for aid, hn, etage, doer in adresser:
        a_flat.append(aid)
        a_flat.append(hn)
        a_flat.append(etage)
        a_flat.append(doer)
    return h_flat, a_flat, resolved


def stage_write():
    husnumre = cache_read(CACHE_HUSNUMMER)
    points = cache_read(CACHE_ADRESSEPUNKTER)
    adresser = cache_read(CACHE_ADRESSER)
    if husnumre is None or points is None or adresser is None:
        sys.exit("husnummer/adressepunkter/adresser cache missing — run those stages first")

    h_flat, a_flat, resolved = build_output(husnumre, points, adresser)
    if not h_flat:
        sys.exit("nothing to write — refusing to write an empty file")

    h_blob = base64.b64encode(gzip.compress(json.dumps(h_flat, separators=(",", ":")).encode(), 9)).decode()
    a_blob = base64.b64encode(gzip.compress(json.dumps(a_flat, separators=(",", ":")).encode(), 9)).decode()
    # Split into two files (#28 follow-up): husnumre is all that's needed to
    # match on typed address text (and all index.html's home/work picker
    # ever needs — building-level only); enheder is only needed to expand a
    # matched house into its individual condo units, and used to be more
    # than half the combined file's bytes for something most lookups never
    # touch.
    husnumre_out = {
        "source": "DAR/v3 via Datafordeler GraphQL — self-hosted address search, no DAWA dependency (#28)",
        "note": "flat [id_lokalId,adgangsadressebetegnelse,lat,lon]*N array — search the text "
                "client-side, id_lokalId is DAWA's old adgangsadresseid/DAR_Husnummer.id_lokalId.",
        "husnumreCount": resolved,
        "husnumreSkippedNoCoord": len(husnumre) - resolved,
        "enc": "gzip+base64",
        "husnumre": h_blob,
    }
    enheder_out = {
        "source": "DAR/v3 via Datafordeler GraphQL — self-hosted address search, no DAWA dependency (#28)",
        "note": "flat [id_lokalId,husnummerParentId,etagebetegnelse,doerbetegnelse]*M array — "
                "id_lokalId here is DAWA's old adresse.id, and matches data/bbr_lookup.json's "
                "adresseIdentificerer keys directly (confirmed live, #26 follow-up), so a picked unit "
                "joins straight into that file with no extra resolution.",
        "enhederCount": len(adresser),
        "enc": "gzip+base64",
        "enheder": a_blob,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_HUSNUMRE, "w", encoding="utf-8") as fh:
        json.dump(husnumre_out, fh, ensure_ascii=False, separators=(",", ":"))
    with open(OUT_ENHEDER, "w", encoding="utf-8") as fh:
        json.dump(enheder_out, fh, ensure_ascii=False, separators=(",", ":"))
    mb_h = os.path.getsize(OUT_HUSNUMRE) / 1e6
    mb_e = os.path.getsize(OUT_ENHEDER) / 1e6
    print(f"wrote data/dar_husnumre.json ({mb_h:.2f} MB) — {resolved} husnumre "
          f"({len(husnumre) - resolved} skipped, no coordinate)")
    print(f"wrote data/dar_enheder.json ({mb_e:.2f} MB) — {len(adresser)} units")
    if mb_h + mb_e > 60:
        sys.exit("too large to commit")


STAGES = {
    "vejids": stage_vejids,
    "husnummer": stage_husnummer,
    "adressepunkter": stage_adressepunkter,
    "adresser": stage_adresser,
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
