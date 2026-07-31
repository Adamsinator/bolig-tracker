#!/usr/bin/env python3
"""Probe v3. The user found the MiljoeGIS tilecache (WMTS). WMTS only serves
images, but its GetCapabilities names the layers — and the layer name is what we
need to find the matching *vector* (WFS) service. Throwaway."""
import re
import urllib.request

UA = {"User-Agent": "bolig-tracker/1.0 (+https://boligtracker.dk)"}


def get(url, cap=3000000):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.read(cap).decode("utf-8", "replace")
    except Exception as ex:
        return f"__ERR__ {ex}"


print("### 1) WMTS tilecache capabilities — layer names ###", flush=True)
b = get("https://tilecache2-miljoegis.mim.dk/gwc/service/wmts?REQUEST=getcapabilities")
noise_layers = []
if b.startswith("__ERR__"):
    print("   ", b[:300])
else:
    print("    bytes:", len(b))
    ids = re.findall(r"<ows:Identifier>([^<]+)</ows:Identifier>", b)
    print("    total identifiers:", len(ids))
    noise_layers = sorted({i for i in ids if re.search(r"stoj|støj|noise|lden|ldn|vejstoj", i, re.I)})
    print("    NOISE-ish layers:", noise_layers[:40])
    if not noise_layers:
        print("    sample identifiers:", ids[:40])

print("\n### 2) try the same names as WFS/WMS on the miljoegis hosts ###", flush=True)
cands = noise_layers[:6] or ["stoej", "stoejkortlaegning"]
seen = set()
for lyr in cands:
    # a GeoServer-backed tilecache usually fronts a workspace:layer name
    ws = lyr.split(":")[0] if ":" in lyr else None
    for base in [
        "https://wfs2-miljoegis.mim.dk/{}/ows".format(ws or lyr),
        "https://miljoegis.mim.dk/wfs?servicename=miljoegis-{}_wfs".format(ws or lyr),
    ]:
        u = base + ("&" if "?" in base else "?") + "service=WFS&version=2.0.0&request=GetCapabilities"
        if u in seen:
            continue
        seen.add(u)
        r = get(u, 400000)
        if r.startswith("__ERR__"):
            print(f"    {u[:95]} -> {r[:60]}")
            continue
        names = re.findall(r"<Name>([^<]+)</Name>", r)
        hits = [n for n in names if re.search(r"stoj|støj|noise|lden", n, re.I)]
        print(f"    {u[:95]} -> {len(names)} featuretypes, noise: {hits[:15]}")

print("\n### 3) if a noise featuretype exists, pull one feature to see attributes ###", flush=True)
for lyr in noise_layers[:3]:
    ws = lyr.split(":")[0] if ":" in lyr else lyr
    u = (f"https://wfs2-miljoegis.mim.dk/{ws}/ows?service=WFS&version=2.0.0&request=GetFeature"
         f"&typeNames={lyr}&count=1&outputFormat=application/json&srsName=EPSG:4326")
    r = get(u, 60000)
    print(f"    {lyr}: ", (r[:400] if not r.startswith("__ERR__") else r[:140]))
