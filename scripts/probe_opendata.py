#!/usr/bin/env python3
"""Temporary probe: what Danish public property data can we actually reach, and
what needs an account?

Answers three questions before we design anything:
  1. Is DAWA still alive? (a reddit thread says it was switched off 1/7/2026,
     four weeks ago, yet our build still pulls kommunegrænser from it)
  2. Which Datafordeleren services respond, and which want credentials?
  3. Is there any open route to tinglyste handelspriser, or to the official
     ejendomsvurdering?

Prints status, content-type and a snippet for each candidate. Nothing is written
and nothing is committed — this exists to be read once and deleted.
"""
import json
import ssl
import sys
import urllib.error
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (compatible; bolig-tracker probe; +https://boligtracker.dk)",
      "Accept": "application/json, application/xml, text/html;q=0.8, */*;q=0.5"}
CTX = ssl.create_default_context()

# Sofievej 11, 2950 Hørsholm — the first-row case we care about
LAT, LON = 55.86308, 12.561188

CANDIDATES = [
    # ---- DAWA: are we living on borrowed time? ----
    ("DAWA adressesøgning",
     "https://api.dataforsyningen.dk/adresser?q=Sofievej%2011%2C%202950&per_side=1"),
    ("DAWA jordstykke (matrikel!) by point",
     f"https://api.dataforsyningen.dk/jordstykker?cirkel={LON},{LAT},60"),
    ("DAWA adgangsadresse -> jordstykke+BFE",
     f"https://api.dataforsyningen.dk/adgangsadresser?cirkel={LON},{LAT},40&struktur=fuld"),
    ("DAWA kommune (what our build uses)",
     "https://api.dataforsyningen.dk/kommuner/223?format=geojson&srid=4326"),
    ("DAWA replikering/udtraek info",
     "https://api.dataforsyningen.dk/replikering/datamodel"),

    # ---- Datafordeleren: which of these want an account? ----
    ("Datafordeler MATRIKLEN WFS GetCapabilities",
     "https://services.datafordeler.dk/MATRIKLEN2/MatrikelGaeldendeOgForeloebigWFS/1.0.0/WFS"
     "?service=WFS&request=GetCapabilities"),
    ("Datafordeler BBR REST bygning",
     "https://services.datafordeler.dk/BBR/BBRPublic/1/rest/bygning?Kommunekode=0223"),
    ("Datafordeler EJENDOMSVURDERING",
     "https://services.datafordeler.dk/EJENDOMSVURDERING/Ejendomsvurdering/1/REST/"
     "HentEjendomsvurderingerForBFE?BFEnummer=1"),
    ("Datafordeler EBR (ejendomsbeliggenhed)",
     "https://services.datafordeler.dk/EBR/Ejendomsbeliggenhed/1/REST/"
     "Ejendomsbeliggenhed?BFEnummer=1"),
    ("Datafordeler DAR REST",
     "https://services.datafordeler.dk/DAR/DAR/3.0.0/REST/husnummer?kommunekode=0223"),
    ("Datafordeler GraphQL (test env, per the reddit thread)",
     "https://test.services.datafordeler.dk/"),
    ("datafordeler.dk front page",
     "https://datafordeler.dk/"),

    # ---- tinglyste handelspriser: is there any open route? ----
    ("Tinglysning front",  "https://www.tinglysning.dk/"),
    ("Tinglysning API host", "https://api.tinglysning.dk/"),
    ("Vurderingsportalen", "https://www.vurderingsportalen.dk/"),

    # ---- docs the thread pointed at ----
    ("SDFI confluence DAR docs",
     "https://confluence.sdfi.dk/pages/viewpage.action?pageId=16056323"),
]


def probe(name, url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
            body = r.read(1200)
            ct = (r.headers.get("Content-Type") or "?").split(";")[0]
            print(f"  {r.status:>3}  {ct:<28} {name}")
            return r.status, body, ct
    except urllib.error.HTTPError as ex:
        body = b""
        try:
            body = ex.read(600)
        except Exception:
            pass
        ct = (ex.headers.get("Content-Type") or "?").split(";")[0] if ex.headers else "?"
        note = "  <- wants credentials" if ex.code in (401, 403) else ""
        print(f"  {ex.code:>3}  {ct:<28} {name}{note}")
        return ex.code, body, ct
    except Exception as ex:
        print(f"  ---  {'':<28} {name}  ({type(ex).__name__}: {str(ex)[:90]})")
        return None, b"", "?"


print("=" * 78)
print("Danish property open-data probe")
print("=" * 78)
results = {}
for name, url in CANDIDATES:
    results[name] = probe(name, url)

# The two that would actually change the design get a closer look.
print("\n" + "=" * 78)
print("Detail on the ones that matter")
print("=" * 78)

for name in ("DAWA jordstykke (matrikel!) by point",
             "DAWA adgangsadresse -> jordstykke+BFE"):
    code, body, _ = results.get(name, (None, b"", ""))
    print(f"\n--- {name} (HTTP {code})")
    if not body:
        print("    no body")
        continue
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        print("    ", body[:500].decode("utf-8", "replace").replace("\n", " ")[:500])
        continue
    first = data[0] if isinstance(data, list) and data else data
    if isinstance(first, dict):
        print("    top-level keys:", sorted(first)[:25])
        for k in ("matrikelnr", "ejerlavnavn", "bfenummer", "registreretareal",
                  "vejareal", "jordstykke", "adgangsadresse"):
            if k in first:
                v = first[k]
                print(f"      {k}: {json.dumps(v, ensure_ascii=False)[:220]}")

print("\n" + "=" * 78)
print("Read this, then delete scripts/probe_opendata.py and its workflow.")
print("=" * 78)
