#!/usr/bin/env python3
"""One-off: pull Miljoestyrelsen's 2022 road-traffic noise mapping and write
data/noise.json — a compact dB grid covering the corridor.

EU noise mapping is redone only every ~5 years, so this runs once rather than in
the daily build. "TAB" on the MiljoeGIS download page is MapInfo TAB (a vector
format), so GDAL does the reading, clipping and reprojection.

The source contours are ~108 MB of GeoJSON for the corridor alone — too big to
commit and slow to query per address. Instead the bands are rasterised to a
20 m grid of dB values, gzipped and base64'd: about a thousandth of the size,
an O(1) lookup per listing, and the daily build needs nothing but the stdlib to
read it.
"""
import base64
import gzip
import io
import json
import math
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile

# mst.dk sits behind a WAF that answers 406 to anything that doesn't look like a
# browser, so send a full, ordinary header set rather than a bare User-Agent.
HDRS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "da,en-US;q=0.7,en;q=0.3",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "close",
    "Upgrade-Insecure-Requests": "1",
}
PAGE = os.environ.get("NOISE_PAGE") or (
    "https://mst.dk/erhverv/tilskud-miljoeviden-og-data/data-og-databaser/"
    "miljoegis-data-om-natur-og-miljoe-paa-webkort/hent-data-udstillet-paa-miljoegis")
# set NOISE_URL to skip the page scrape and go straight at a known archive
DIRECT = os.environ.get("NOISE_URL", "").strip()
# NOISE_PROBE=1 stops after reporting the chosen URL and its size — seconds, not
# an hour, when all you need to know is what the job is about to pull
PROBE = os.environ.get("NOISE_PROBE", "").strip() not in ("", "0", "false")
# NOISE_LAYER pins which layers to use, by comma-separated filename substrings
LAYER = os.environ.get("NOISE_LAYER", "").strip()
# the 1.1 GB archive survives between runs here, so a retry costs a minute
CACHE = os.environ.get("NOISE_CACHE", "/tmp/noise-cache/noise.zip")

# corridor: Koebenhavn -> Hilleroed / Frederikssund / the coast (Region Hovedstaden)
W, S, E, N = 12.05, 55.58, 12.70, 55.96
RES_M = 20.0                       # grid resolution in metres
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "noise.json")

# Danish road layers at 1.5 m facade height, daytime Lden. The archive also holds
# the EU reporting variants at 4 m, the "_nat" night maps, and rail / airport /
# industry — none of which is what a buyer means by road noise outside the house.
# Roads inside agglomerations and major roads outside them live in separate
# layers, so take them all and keep the loudest reading at each point.
ROAD_DAY = ["dk_2022_vej_1_5m", "dk_2022_stoerre_veje_1_5m", "dk_2022_sogb_veje_1_5m"]
VEC_EXT = (".tab", ".shp", ".gpkg", ".mif")


def fetch(url, referer=None):
    h = dict(HDRS)
    if referer:
        h["Referer"] = referer
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=600) as r:
        raw = r.read()
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return raw


def head(url, referer=None):
    """Size + type without pulling the body, so a huge archive is a known cost
    rather than a job that silently sits there for an hour."""
    h = dict(HDRS)
    if referer:
        h["Referer"] = referer
    req = urllib.request.Request(url, headers=h, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.headers.get("Content-Length"), r.headers.get("Content-Type")
    except Exception as ex:
        return None, None, f"HEAD failed: {ex}"


def download(url, dest, referer=None):
    """Streamed, with progress — a 45-minute silent read tells you nothing about
    whether it is slow or wedged."""
    h = dict(HDRS)
    if referer:
        h["Referer"] = referer
    req = urllib.request.Request(url, headers=h)
    got, step = 0, 100 * 1024 * 1024
    nxt = step
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as fh:
        total = r.headers.get("Content-Length")
        total = int(total) if total and total.isdigit() else None
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            got += len(chunk)
            if got >= nxt:
                pct = f" ({got*100//total}%)" if total else ""
                print(f"   … {got/1e6:.0f} MB{pct}", flush=True)
                nxt += step
    os.replace(part, dest)         # only a complete file becomes the cache entry
    return got


DB_MIN, DB_MAX = 40, 95            # sanity range for a facade Lden reading


def _from_lo(lo):
    """A band's lower bound to the single number we store. Bands are 5 dB wide,
    so the midpoint is lower+2 — and the open-ended top band gets the same step
    rather than pretending to know how loud it really is."""
    if lo is None or not (DB_MIN <= lo <= DB_MAX):
        return None
    return int(lo) + 2


def band_db(category):
    """The eu_* layers label the band in one string: 'Lden5559' -> 57, 'Lden75'
    -> 77."""
    d = "".join(re.findall(r"\d+", str(category or "")))
    if len(d) >= 4:
        return _from_lo(int(d[:2]))
    if len(d) == 2:
        return _from_lo(int(d))
    return None


def iso_db(a, b):
    """The dk_* layers carry the band as two numeric bounds (isov1/isov2, or
    iso1/iso2). Take the lower of the two — which end is which varies by layer."""
    vals = []
    for v in (a, b):
        try:
            if v is not None and str(v).strip() != "":
                vals.append(float(v))
        except (TypeError, ValueError):
            pass
    vals = [v for v in vals if DB_MIN <= v <= DB_MAX]
    return _from_lo(min(vals)) if vals else None


def feature_db(feat, cat_field, iso_pair):
    if cat_field:
        return band_db(feat.GetField(cat_field))
    if iso_pair:
        return iso_db(feat.GetField(iso_pair[0]), feat.GetField(iso_pair[1]))
    return None


# ---------------------------------------------------------------- 1) locate
if DIRECT:
    url = DIRECT
    print("1) NOISE_URL given, skipping the page scrape", flush=True)
else:
    print("1) reading the download page…", flush=True)
    try:
        html = fetch(PAGE).decode("utf-8", "replace")
    except Exception as ex:
        sys.exit(f"could not read the page: {ex}")

    links = [urllib.parse.urljoin(PAGE, l) for l in re.findall(r'href=["\']([^"\']+)["\']', html)]
    cand = [l for l in links if re.search(r"(noise|stoej|støj)", l, re.I)]
    print(f"   {len(links)} links, {len(cand)} noise-ish")

    def score(u):
        s = 0
        if "2022" in u: s += 4
        if re.search(r"tab", u, re.I): s += 2
        if u.lower().endswith(".zip"): s += 2
        return s

    best = sorted(set(cand), key=score, reverse=True)
    if not best or score(best[0]) < 4:
        print("\n   No obvious noise-2022 download link found on the page.")
        for l in sorted({l for l in links if re.search(r"download|\.zip", l, re.I)})[:80]:
            print("     ", l)
        sys.exit("stopping: need the exact download URL")
    for l in best[:6]:
        print(f"     {score(l):>3}  {l}")
    url = best[0]

print(f"\n2) target: {url}", flush=True)
st, clen, ctype = head(url, referer=PAGE)
want = int(clen) if clen and clen.isdigit() else None
print(f"   HEAD {st} · {(want/1e6):.1f} MB · {ctype}" if want else f"   HEAD {st} · ? · {ctype}",
      flush=True)
if PROBE:
    sys.exit(0)

if os.path.exists(CACHE) and (want is None or abs(os.path.getsize(CACHE) - want) < 1024):
    print(f"   cache hit: {CACHE} ({os.path.getsize(CACHE)/1e6:.1f} MB) — not re-downloading",
          flush=True)
else:
    got = download(url, CACHE, referer=PAGE)
    print(f"   downloaded {got/1e6:.1f} MB", flush=True)

# ---------------------------------------------------------------- 3) index
print("\n3) reading the archive index…", flush=True)
try:
    z = zipfile.ZipFile(CACHE)
except Exception as ex:
    sys.exit(f"not a zip we can read: {ex}")
infos = [i for i in z.infolist() if not i.is_dir()]
vecs = [i for i in infos if i.filename.lower().endswith(VEC_EXT)]
if not vecs:
    sys.exit(f"no {'/'.join(VEC_EXT)} in the archive ({len(infos)} entries)")
print(f"   {len(infos)} entries, {len(vecs)} vector layers:")
for i in sorted(vecs, key=lambda i: i.filename):
    print(f"     {i.file_size/1e6:8.1f} MB  {i.filename}")

wanted = [s.strip().lower() for s in LAYER.split(",") if s.strip()] if LAYER else ROAD_DAY
chosen = []
for w in wanted:
    hit = [i for i in vecs if w in i.filename.lower()]
    if not hit:
        print(f"   !! no layer matches {w!r}", file=sys.stderr)
        continue
    chosen.append(sorted(hit, key=lambda i: i.file_size, reverse=True)[0])
if not chosen:
    sys.exit(f"none of {wanted} matched any of the {len(vecs)} layers")
print("   using:", [os.path.basename(i.filename) for i in chosen], flush=True)

# Extract only those layers and their sidecars. MapInfo TAB splits one layer over
# several files sharing a stem, and the whole archive unpacked is many GB.
os.makedirs("/tmp/noise", exist_ok=True)
srcs = []
for c in chosen:
    stem = os.path.splitext(c.filename)[0]
    for i in [m for m in infos if os.path.splitext(m.filename)[0] == stem]:
        z.extract(i, "/tmp/noise")
    srcs.append(os.path.join("/tmp/noise", c.filename))

# ---------------------------------------------------------------- 4) clip
from osgeo import gdal, ogr, osr                               # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

print("\n4) clipping each layer to the corridor…", flush=True)
clipped = []
for src in srcs:
    dst = f"/tmp/{os.path.splitext(os.path.basename(src))[0]}_clip.gpkg"
    if os.path.exists(dst):
        os.remove(dst)
    r = subprocess.run([
        "ogr2ogr", "-f", "GPKG", dst, src,
        "-t_srs", "EPSG:4326",
        "-clipdst", str(W), str(S), str(E), str(N),
        "-nlt", "PROMOTE_TO_MULTI",
        "-nln", "noise",
    ], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"   !! ogr2ogr failed on {os.path.basename(src)}: {r.stderr[-600:]}", file=sys.stderr)
        continue
    ds = ogr.Open(dst, 1)
    lyr = ds.GetLayer(0)
    n = lyr.GetFeatureCount()
    if not n:
        print(f"   {os.path.basename(src)}: 0 features in the corridor — skipping")
        ds = None
        continue

    fields = [lyr.GetLayerDefn().GetFieldDefn(i).GetName()
              for i in range(lyr.GetLayerDefn().GetFieldCount())]
    # Two schemas ship in the same archive: the eu_* layers label the band in a
    # single 'category' string, the dk_* ones give it as two numeric bounds
    # (isov1/isov2 or iso1/iso2).
    cat = next((f for f in fields if f.lower() == "category"), None)
    if not cat:
        cat = next((f for f in fields if re.search(r"lden|categor|klasse|niveau", f, re.I)), None)
    iso_pair = None
    if not cat:
        lo_f = next((f for f in fields if re.fullmatch(r"iso_?v?1", f, re.I)), None)
        hi_f = next((f for f in fields if re.fullmatch(r"iso_?v?2", f, re.I)), None)
        if lo_f and hi_f:
            iso_pair = (lo_f, hi_f)
    if not cat and not iso_pair:
        print(f"   !! {os.path.basename(src)}: no band field in {fields}", file=sys.stderr)
        ds = None
        continue

    # show the raw values before trusting them — a silent mis-parse here is what
    # produced 10812 nulls last time
    lyr.ResetReading()
    sample = []
    for k, feat in enumerate(lyr):
        if k >= 3:
            break
        sample.append({f: feat.GetField(f) for f in (list(cat and [cat] or []) + list(iso_pair or []))})
    print(f"   {os.path.basename(src)}: band field "
          f"{cat or '/'.join(iso_pair)} · sample {sample}", flush=True)

    # turn the band label into a number GDAL can burn. Wrap the writes in one
    # transaction — GPKG otherwise commits per row, which turns tens of thousands
    # of features into a very long wait.
    lyr.CreateField(ogr.FieldDefn("db", ogr.OFTInteger))
    seen, bad = {}, 0
    lyr.ResetReading()
    lyr.StartTransaction()
    try:
        for feat in lyr:
            v = feature_db(feat, cat, iso_pair)
            if v is None:
                bad += 1
                continue
            feat.SetField("db", v)
            lyr.SetFeature(feat)
            seen[v] = seen.get(v, 0) + 1
        lyr.CommitTransaction()
    except Exception:
        lyr.RollbackTransaction()
        raise
    ds = None
    bands = {k: seen[k] for k in sorted(seen)}
    print(f"   {os.path.basename(src)}: {n} features · bands {bands}"
          + (f" · {bad} unparsed" if bad else ""), flush=True)
    if seen:
        clipped.append(dst)

if not clipped:
    sys.exit("no clipped layer carried a usable dB band — cannot build the grid")

# ---------------------------------------------------------------- 5) rasterise
clat = math.cos(math.radians((S + N) / 2))
cols = int(round((E - W) * 111320.0 * clat / RES_M))
rows = int(round((N - S) * 110540.0 / RES_M))
print(f"\n5) rasterising to {cols}×{rows} at ~{RES_M:.0f} m…", flush=True)

mem = gdal.GetDriverByName("MEM").Create("", cols, rows, 1, gdal.GDT_Byte)
mem.SetGeoTransform((W, (E - W) / cols, 0, N, 0, -(N - S) / rows))
srs = osr.SpatialReference()
srs.ImportFromEPSG(4326)
mem.SetProjection(srs.ExportToWkt())
mem.GetRasterBand(1).Fill(0)

# Bands nest: the 70 dB contour sits inside the 55 dB one. Burning in ascending
# order leaves the loudest value on top, which is the reading you actually want.
levels = set()
for path in clipped:
    ds = ogr.Open(path)
    lyr = ds.GetLayer(0)
    lyr.ResetReading()
    for feat in lyr:
        v = feat.GetField("db")
        if v:
            levels.add(int(v))
    ds = None
for v in sorted(levels):
    for path in clipped:
        gdal.Rasterize(mem, path, options=gdal.RasterizeOptions(
            bands=[1], burnValues=[v], where=f"db = {v}", allTouched=True))
    print(f"   burned {v} dB", flush=True)

grid = mem.GetRasterBand(1).ReadAsArray().tobytes()
mem = None

hist = {}
for b in grid:
    if b:
        hist[b] = hist.get(b, 0) + 1
covered = sum(hist.values())
print(f"   {covered*100.0/len(grid):.1f}% of the grid has a reading; "
      f"cells per band { {k: hist[k] for k in sorted(hist)} }")
if not covered:
    sys.exit("the grid came out empty — nothing burned")

# ---------------------------------------------------------------- 6) write
blob = base64.b64encode(gzip.compress(grid, 9)).decode("ascii")
out = {
    "source": "Miljøstyrelsen / MiljøGIS — EU-støjkortlægning 2022, vejstøj Lden 1,5 m",
    "url": url,
    "layers": [os.path.basename(p) for p in clipped],
    "bbox": [W, S, E, N],
    "cols": cols, "rows": rows, "resM": RES_M,
    "note": "db = 0 means no mapped band (quieter than the 55 dB Lden floor)",
    "enc": "gzip+base64",
    "data": blob,
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
mb = os.path.getsize(OUT) / 1e6
print(f"\n6) wrote data/noise.json — {cols}×{rows} grid, {mb:.2f} MB", flush=True)
if mb > 45:
    sys.exit("refusing to commit: grid is too large, raise RES_M")
