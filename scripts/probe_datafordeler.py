#!/usr/bin/env python3
"""Temporary probe #6: what is Matriklen called on Datafordeler's GraphQL?

BBR/v3 and DAR/v3 answer 200. MATRIKLEN/v3 and EJENDOMSVURDERING/v3 answer 404
— a wrong path, not denied access — so the registers are almost certainly there
under different names. This tries the plausible ones and reports which resolve.

Matters because #25 (first row to the water) needs parcel geometry, and the
obvious source, DAWA, is switched off 1 October 2026.
"""
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

KEY = os.environ.get("DATAFORDELER_API", "").strip()
if not KEY:
    sys.exit("no DATAFORDELER_API")
SECRETS = [KEY] + [os.environ.get(k, "") for k in
                   ("DATAFORDELER_USERID", "DATAFORDELER_USER", "DATAFORDELER_PASS")]


def redact(t):
    out = str(t)
    for s in SECRETS:
        if s:
            out = out.replace(s, "***").replace(urllib.parse.quote(s, safe=""), "***")
    return out


GQL = "https://graphql.datafordeler.dk"
EK = urllib.parse.quote(KEY, safe="")
UA = {"User-Agent": "bolig-tracker/1.0 (+https://boligtracker.dk)", "Accept": "*/*"}

CANDIDATES = [
    "MAT", "MATRIKEL", "MATRIKLEN", "MATRIKLEN2", "Matriklen", "Matrikel",
    "MU", "MATRIKELKORT", "MATRIKELREGISTER",
    "EJENDOMSVURDERING", "VUR", "EJENDOMSVURDERINGEN", "VURDERING",
    "EBR", "EJF", "EJERFORTEGNELSEN", "ESR", "BBR", "DAR", "DAGI", "CVR", "CPR",
]
VERSIONS = ["v3", "v2", "v1"]


def get(url, cap=2500):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            return r.status, r.read(cap)
    except urllib.error.HTTPError as ex:
        return ex.code, b""
    except Exception as ex:
        return None, f"{type(ex).__name__}".encode()


print("=== which registers resolve? ===")
found = {}
for name in CANDIDATES:
    for v in VERSIONS:
        code, body = get(f"{GQL}/{name}/{v}/schema?apiKey={EK}")
        if code == 200:
            types = re.findall(r"type\s+(\w+)\s*\{", body.decode("utf-8", "replace"))
            print(f"  200  {name}/{v}    types: {types[:6]}")
            found[name] = v
            break
        if code not in (404, None):
            print(f"  {code}  {name}/{v}")
    else:
        continue

print(f"\nresolved: {sorted(found)}")

# For anything matrikel-ish that resolved, pull more of the schema to find the
# entity that actually carries parcel geometry.
for name, v in found.items():
    if not re.search(r"mat|jord|ejendom", name, re.I):
        continue
    print(f"\n=== {name}/{v}: looking for a parcel entity ===")
    code, body = get(f"{GQL}/{name}/{v}/schema?apiKey={EK}", 200000)
    txt = body.decode("utf-8", "replace")
    types = re.findall(r"type\s+(\w+)\s*\{", txt)
    hits = [t for t in types if re.search(r"jordstykke|samletfastejendom|matrikel|geometri",
                                          t, re.I)]
    print(f"  {len(types)} types, {len(hits)} parcel-ish: {hits[:12]}")
    for t in hits[:2]:
        m = re.search(r"type\s+" + re.escape(t) + r"\s*\{(.{0,900})", txt, re.S)
        if m:
            fields = re.findall(r"^\s{2,}(\w+):", m.group(1), re.M)
            print(f"    {t} fields: {fields[:20]}")

print("\nDelete this script and its workflow once read.")
