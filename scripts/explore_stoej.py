#!/usr/bin/env python3
"""Throwaway probe: find Miljoestyrelsen's traffic-noise layers and whether they
are available as vector (WFS) so we can do point-in-polygon per address.
Delete once the real fetch is landed."""
import re
import sys
import urllib.request

UA = {"User-Agent": "bolig-tracker/1.0 (+https://boligtracker.dk)"}


def get(url, n=4000):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read(n * 40).decode("utf-8", "replace")
    except Exception as ex:
        return f"__ERR__ {ex}"


CAPS = [
    # Miljoestyrelsen MiljoeGIS — the site behind ?profile=noise
    "https://miljoegis.mim.dk/wms?servicename=miljoegis-mst_wms&service=wms&request=GetCapabilities",
    "https://miljoegis.mim.dk/wms?servicename=miljoegis-stoej_wms&service=wms&request=GetCapabilities",
    "https://miljoegis.mim.dk/wms?servicename=miljoegis-stoejkortlaegning_wms&service=wms&request=GetCapabilities",
    "https://miljoegis.mim.dk/wfs?servicename=miljoegis-stoej_wfs&service=wfs&request=GetCapabilities",
    # Danmarks Miljoeportal / Danmarks Arealinformation
    "https://wfs2-miljoegis.mim.dk/dai/ows?service=WFS&version=1.0.0&request=GetCapabilities",
    "https://arealdata-api.miljoeportal.dk/gis/services/dai/MapServer/WFSServer?service=WFS&request=GetCapabilities",
]

for url in CAPS:
    body = get(url)
    print("\n=== " + url)
    if body.startswith("__ERR__"):
        print("   ", body[:200])
        continue
    print("    bytes:", len(body), "| looks like XML:", body.lstrip()[:1].startswith("<"))
    names = re.findall(r"<(?:wms:|wfs:)?Name>([^<]+)</(?:wms:|wfs:)?Name>", body)
    titles = re.findall(r"<(?:wms:|wfs:)?Title>([^<]+)</(?:wms:|wfs:)?Title>", body)
    hits = [n for n in names if re.search(r"stoj|støj|noise|lden|ldn", n, re.I)]
    thits = [t for t in titles if re.search(r"stoj|støj|noise|lden", t, re.I)]
    print("    layers total:", len(names), "| noise-ish names:", hits[:25])
    print("    noise-ish titles:", thits[:15])
    if not hits and names:
        print("    sample names:", names[1:16])

print("\n=== does the noise profile page reveal a service name? ===")
profile = get("https://miljoegis.mim.dk/?profile=noise", 3000)
if profile.startswith("__ERR__"):
    print(profile[:200])
else:
    svc = sorted(set(re.findall(r"[A-Za-z0-9_\-]*stoej[A-Za-z0-9_\-]*|miljoegis-[a-z0-9_\-]+", profile, re.I)))
    print("   service-ish tokens:", svc[:30])
