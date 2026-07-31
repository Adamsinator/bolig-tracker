#!/usr/bin/env python3
"""One-off: pull Miljoestyrelsen's 2022 road-traffic noise mapping, clip it to the
corridor and write data/noise.json (GeoJSON, EPSG:4326).

EU noise mapping is redone only every ~5 years, so this runs once rather than in
the daily build. "TAB" on the MiljoeGIS download page is MapInfo TAB (a vector
format), so GDAL does the reading, clipping and reprojection.
"""
import gzip
import io
import json
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
# corridor: Koebenhavn -> Hilleroed / Frederikssund / the coast (Region Hovedstaden)
W, S, E, N = 12.05, 55.58, 12.70, 55.96
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "noise.json")


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


if DIRECT:
    url = DIRECT
    print(f"1) NOISE_URL given, skipping the page scrape", flush=True)
else:
    print("1) reading the download page…", flush=True)
    try:
        html = fetch(PAGE).decode("utf-8", "replace")
    except Exception as ex:
        sys.exit(f"could not read the page: {ex}")

    links = re.findall(r'href=["\']([^"\']+)["\']', html)
    links = [urllib.parse.urljoin(PAGE, l) for l in links]
    cand = [l for l in links if re.search(r"(noise|stoej|støj)", l, re.I)]
    print(f"   {len(links)} links, {len(cand)} noise-ish:")
    for l in sorted(set(cand))[:60]:
        print("     ", l)

    # prefer 2022 road/vej TAB archives
    def score(u):
        s = 0
        if "2022" in u: s += 4
        if re.search(r"vej|road", u, re.I): s += 3
        if re.search(r"tab", u, re.I): s += 2
        if u.lower().endswith(".zip"): s += 2
        if re.search(r"bane|jernbane|rail", u, re.I): s -= 2
        if re.search(r"fly|air|industri", u, re.I): s -= 2
        return s

    best = sorted(set(cand), key=score, reverse=True)
    if not best or score(best[0]) < 4:
        print("\n   No obvious noise-2022 download link found on the page.")
        print("   All links containing 'download'/'.zip':")
        for l in sorted({l for l in links if re.search(r"download|\.zip", l, re.I)})[:80]:
            print("     ", l)
        sys.exit("stopping: need the exact download URL")
    url = best[0]

print(f"\n2) downloading {url}", flush=True)
data = fetch(url, referer=PAGE)
print(f"   {len(data)/1e6:.1f} MB", flush=True)
zp = "/tmp/noise.zip"
open(zp, "wb").write(data)

print("3) unpacking…", flush=True)
os.makedirs("/tmp/noise", exist_ok=True)
try:
    with zipfile.ZipFile(zp) as z:
        z.extractall("/tmp/noise")
        names = z.namelist()
except Exception as ex:
    sys.exit(f"not a zip we can read: {ex}")
print("   entries:", names[:15], "…" if len(names) > 15 else "")

found = []
for root, _dirs, files in os.walk("/tmp/noise"):
    for f in files:
        if f.lower().endswith((".tab", ".shp", ".gpkg", ".mif")):
            found.append(os.path.join(root, f))
if not found:
    sys.exit("no .tab/.shp/.gpkg found in the archive")
print("   vector files:", [os.path.basename(f) for f in found][:20])
# several layers may ship together (day / night / rail) — take the road Lden one
def pick(p):
    b = os.path.basename(p).lower()
    s = os.path.getsize(p) / 1e6
    return (3 if re.search(r"vej|road", b) else 0) + (2 if "lden" in b else 0) \
        - (3 if re.search(r"nat|night|bane|rail|fly|air", b) else 0) + min(s / 100, 1)
src = sorted(found, key=pick, reverse=True)[0]
print("   using:", src, flush=True)

print("4) inspecting layer…", flush=True)
print(subprocess.run(["ogrinfo", "-so", "-al", src], capture_output=True, text=True).stdout[:1800])

print("5) clipping to the corridor + reprojecting to WGS84…", flush=True)
tmp = "/tmp/noise_clip.json"
if os.path.exists(tmp):
    os.remove(tmp)
r = subprocess.run([
    "ogr2ogr", "-f", "GeoJSON", tmp, src,
    "-t_srs", "EPSG:4326",
    "-clipdst", str(W), str(S), str(E), str(N),
    "-simplify", "0.00005",
    "-nlt", "PROMOTE_TO_MULTI",
], capture_output=True, text=True)
if r.returncode != 0:
    print(r.stdout[-1500:]); sys.exit("ogr2ogr failed: " + r.stderr[-1500:])

gj = json.load(open(tmp, encoding="utf-8"))
feats = gj.get("features", [])
print(f"   {len(feats)} features after clipping")
if feats:
    print("   attributes:", json.dumps(feats[0].get("properties", {}), ensure_ascii=False)[:400])

# keep only the geometry + the dB band attribute, rounded, to stay small
out = {"type": "FeatureCollection", "features": []}
for f in feats:
    p = f.get("properties") or {}
    db = None
    for k, v in p.items():
        if re.search(r"lden|db|niveau|klasse|interval|value", str(k), re.I) and v not in (None, ""):
            db = v
            break
    out["features"].append({"type": "Feature", "properties": {"db": db}, "geometry": f.get("geometry")})

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
mb = os.path.getsize(OUT) / 1e6
print(f"6) wrote data/noise.json — {len(out['features'])} features, {mb:.1f} MB")
if mb > 45:
    print("   WARNING: large for a git repo; consider a coarser -simplify")
