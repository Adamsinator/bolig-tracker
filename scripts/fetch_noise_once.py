#!/usr/bin/env python3
"""One-off: pull Miljoestyrelsen's 2022 road-traffic noise mapping, clip it to the
corridor and write data/noise.json (GeoJSON, EPSG:4326).

EU noise mapping is redone only every ~5 years, so this runs once rather than in
the daily build. "TAB" on the MiljoeGIS download page is MapInfo TAB (a vector
format), so GDAL does the reading, clipping and reprojection.
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile

UA = {"User-Agent": "Mozilla/5.0 (compatible; bolig-tracker/1.0; +https://boligtracker.dk)"}
PAGE = ("https://mst.dk/erhverv/tilskud-miljoeviden-og-data/data-og-databaser/"
        "miljoegis-data-om-natur-og-miljoe-paa-webkort/hent-data-udstillet-paa-miljoegis")
# corridor: Koebenhavn -> Hilleroed / Frederikssund / the coast (Region Hovedstaden)
W, S, E, N = 12.05, 55.58, 12.70, 55.96
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "noise.json")


def fetch(url, cap=None):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read(cap) if cap else r.read()


print("1) reading the download page…", flush=True)
try:
    html = fetch(PAGE).decode("utf-8", "replace")
except Exception as ex:
    sys.exit(f"could not read the page: {ex}")

links = re.findall(r'href=["\']([^"\']+)["\']', html)
links = [urllib.parse.urljoin(PAGE, l) for l in links]
cand = [l for l in links if re.search(r"(noise|stoej|støj)", l, re.I)]
print(f"   {len(links)} links, {len(cand)} noise-ish:")
for l in sorted(set(cand))[:40]:
    print("     ", l)

# prefer 2022 road/vej TAB archives
def score(u):
    s = 0
    if "2022" in u: s += 4
    if re.search(r"vej|road", u, re.I): s += 3
    if re.search(r"tab", u, re.I): s += 2
    if u.lower().endswith(".zip"): s += 2
    if re.search(r"bane|jernbane", u, re.I): s -= 2
    return s


best = sorted(set(cand), key=score, reverse=True)
if not best or score(best[0]) < 4:
    print("\n   No obvious noise-2022 download link found on the page.")
    print("   All links containing 'download'/'.zip':")
    for l in sorted({l for l in links if re.search(r"download|\.zip", l, re.I)})[:60]:
        print("     ", l)
    sys.exit("stopping: need the exact download URL")

url = best[0]
print(f"\n2) downloading {url}", flush=True)
data = fetch(url)
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

src = None
for root, _dirs, files in os.walk("/tmp/noise"):
    for f in files:
        if f.lower().endswith((".tab", ".shp", ".gpkg", ".mif")):
            src = os.path.join(root, f)
            break
    if src:
        break
if not src:
    sys.exit("no .tab/.shp/.gpkg found in the archive")
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
"""
"""
