#!/usr/bin/env python3
"""Probe v2 for traffic-noise polygons. Rather than guessing WMS service names,
read the MiljoeGIS noise profile's own config, and check the EEA's Europe-wide
END noise dataset as a fallback. Throwaway."""
import json
import re
import urllib.request

UA = {"User-Agent": "bolig-tracker/1.0 (+https://boligtracker.dk)"}


def get(url, cap=400000):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read(cap).decode("utf-8", "replace")
    except Exception as ex:
        return f"__ERR__ {ex}"


print("### 1) Miljoestyrelsen master WMS — full layer list, noise filtered ###", flush=True)
b = get("https://miljoegis.mim.dk/wms?servicename=miljoegis-mst_wms&service=wms&request=GetCapabilities")
if b.startswith("__ERR__"):
    print("   ", b[:200])
else:
    names = re.findall(r"<Name>([^<]+)</Name>", b)
    print("    total layers:", len(names))
    hits = [n for n in names if re.search(r"stoj|støj|noise|lden|ldn", n, re.I)]
    print("    noise layers:", hits[:30])
    print("    first 25 names:", names[:25])

print("\n### 2) the noise profile page + any JS config it references ###", flush=True)
page = get("https://miljoegis.mim.dk/spatialmap?profile=noise")
if page.startswith("__ERR__"):
    page = get("https://miljoegis.mim.dk/?profile=noise")
if page.startswith("__ERR__"):
    print("   ", page[:200])
else:
    print("    page bytes:", len(page))
    toks = sorted(set(re.findall(r"miljoegis[-_][a-z0-9_\-]+", page, re.I)))
    print("    servicename tokens:", toks[:30])
    srcs = sorted(set(re.findall(r'[\w./\-]+\.js', page)))[:12]
    print("    scripts:", srcs)
    for s in srcs:
        if not re.search(r"config|profile|noise|app", s, re.I):
            continue
        u = s if s.startswith("http") else "https://miljoegis.mim.dk/" + s.lstrip("/")
        js = get(u, 300000)
        if js.startswith("__ERR__"):
            continue
        t = sorted(set(re.findall(r"miljoegis[-_][a-z0-9_\-]+", js, re.I)))
        n = sorted(set(re.findall(r"[A-Za-z0-9_\-]*(?:stoj|noise)[A-Za-z0-9_\-]*", js, re.I)))
        if t or n:
            print(f"    [{u.split('/')[-1]}] services={t[:12]} noiseish={n[:12]}")

print("\n### 3) EEA Europe-wide END noise contours (fallback source) ###", flush=True)
for u in [
    "https://discomap.eea.europa.eu/arcgis/rest/services?f=json",
    "https://discomap.eea.europa.eu/arcgis/rest/services/NOISE?f=json",
]:
    b = get(u, 200000)
    if b.startswith("__ERR__"):
        print("   ", u, b[:120]); continue
    try:
        d = json.loads(b)
        folders = d.get("folders", [])
        svcs = [s.get("name") for s in d.get("services", [])]
        print("   ", u.split("/services")[-1] or "/root", "| folders:",
              [f for f in folders if re.search(r"noise|nois", f, re.I)] or folders[:12])
        print("      services:", [s for s in svcs if re.search(r"noise|nois", str(s), re.I)][:12])
    except Exception as ex:
        print("   ", u, "parse failed", ex)
