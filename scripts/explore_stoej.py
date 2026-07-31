#!/usr/bin/env python3
"""Probe v4. v3 found the real layer names (theme-dk_noise2022_*). Now: list them
all, find the WMS servicename that serves them, and test GetFeatureInfo — which
returns the dB value at a coordinate. Throwaway."""
import re
import urllib.request

UA = {"User-Agent": "bolig-tracker/1.0 (+https://boligtracker.dk)"}
# a point right beside the Helsingoermotorvejen, where noise must be high
TEST = (55.7570, 12.5140)


def get(url, cap=4000000):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.read(cap).decode("utf-8", "replace")
    except Exception as ex:
        return f"__ERR__ {ex}"


print("### 1) ALL noise layers in the tilecache ###", flush=True)
b = get("https://tilecache2-miljoegis.mim.dk/gwc/service/wmts?REQUEST=getcapabilities")
layers = []
if not b.startswith("__ERR__"):
    ids = re.findall(r"<ows:Identifier>([^<]+)</ows:Identifier>", b)
    layers = sorted({i for i in ids if re.search(r"noise|stoj|støj", i, re.I)})
    for l in layers:
        print("   ", l)
    print("    total noise layers:", len(layers))
else:
    print("   ", b[:200])

# road-traffic layers are the ones we actually want (vej), not railway (bane)
road = [l for l in layers if re.search(r"vej|road", l, re.I)] or layers
print("\n    road-ish candidates:", road[:8])

print("\n### 2) find a WMS servicename that serves these layers ###", flush=True)
SERVICES = [
    "miljoegis-noise_wms", "miljoegis-noise2022_wms", "miljoegis-dk_noise2022_wms",
    "miljoegis-stoej2022_wms", "miljoegis-stoejkort_wms", "miljoegis-eustoej_wms",
]
working = []
for s in SERVICES:
    u = f"https://miljoegis.mim.dk/wms?servicename={s}&service=wms&request=GetCapabilities"
    r = get(u, 900000)
    if r.startswith("__ERR__"):
        print(f"    {s:32} -> {r[:50]}")
        continue
    names = re.findall(r"<Name>([^<]+)</Name>", r)
    hits = [n for n in names if re.search(r"noise|stoj", n, re.I)]
    print(f"    {s:32} -> {len(names)} layers, noise: {len(hits)} {hits[:4]}")
    if hits:
        working.append((s, hits))

print("\n### 3) GetFeatureInfo — can we read a dB value at a point? ###", flush=True)
lat, lon = TEST
d = 0.002
bbox = f"{lon-d},{lat-d},{lon+d},{lat+d}"
targets = [(s, l) for s, hits in working for l in hits[:2]]
if not targets and road:
    targets = [("miljoegis-mst_wms", road[0])]
for s, lyr in targets[:6]:
    for fmt in ("application/json", "text/plain"):
        u = (f"https://miljoegis.mim.dk/wms?servicename={s}&service=WMS&version=1.3.0"
             f"&request=GetFeatureInfo&layers={lyr}&query_layers={lyr}"
             f"&crs=EPSG:4326&bbox={lat-d},{lon-d},{lat+d},{lon+d}"
             f"&width=101&height=101&i=50&j=50&info_format={fmt}")
        r = get(u, 6000)
        print(f"    [{fmt}] {lyr[:44]} -> {r[:220] if not r.startswith('__ERR__') else r[:90]}")
        if not r.startswith("__ERR__") and len(r) > 40:
            break
