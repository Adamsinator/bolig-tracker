#!/usr/bin/env python3
"""Temporary probe: parse the MAT schema SDL to find the parcel geometry field.

Two dead ends first, both mine:
  - v1 printed the body only on non-200, so GraphQL's 200-with-errors was
    swallowed and it reported "NONE FOUND".
  - v2 switched to __type introspection, which the endpoint refuses outright
    ("Introspection is not allowed for the current request.").

The documented route was right there: GET /{REGISTER}/{version}/schema?apiKey=...
serves the SDL, and it answered 200 earlier. The original regex over it failed
only because GraphQL descriptions are triple-quoted blocks full of words like
"type:" and "URI:" — strip those first and the schema parses cleanly.
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
EK = urllib.parse.quote(KEY, safe="")
UA = {"User-Agent": "bolig-tracker/1.0", "Accept": "*/*"}


def fetch(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read(500)
    except Exception as ex:
        return None, f"{type(ex).__name__}: {str(ex)[:80]}".encode()


code, raw = fetch(f"https://graphql.datafordeler.dk/MAT/v2/schema?apiKey={EK}")
print(f"schema fetch: HTTP {code}, {len(raw)} bytes")
if code != 200:
    sys.exit(f"cannot read the schema: {raw[:300]!r}")

sdl = raw.decode("utf-8", "replace")
# GraphQL descriptions are triple-quoted and full of 'type:' / 'URI:' lines —
# they are what made the first parse pick up documentation instead of fields.
clean = re.sub(r'"""(?:.|\n)*?"""', "", sdl)
print(f"after stripping descriptions: {len(clean)} chars")


def block(name, kw="type"):
    m = re.search(r"\b%s\s+%s\s*\{" % (kw, re.escape(name)), clean)
    if not m:
        return None
    i, depth = m.end(), 1
    while i < len(clean) and depth:
        if clean[i] == "{":
            depth += 1
        elif clean[i] == "}":
            depth -= 1
        i += 1
    return clean[m.end():i - 1]


def fields_of(body):
    if not body:
        return []
    out = []
    for line in body.splitlines():
        line = line.strip()
        m = re.match(r"^(\w+)\s*(\([^)]*\))?\s*:\s*([\[\]\w!]+)", line)
        if m:
            out.append((m.group(1), m.group(3)))
    return out


print("\n=== the MAT_Jordstykke query-root field ===")
m = re.search(r"^\s*MAT_Jordstykke\s*\(([^)]*)\)\s*:\s*([\[\]\w!]+)", clean, re.M)
if m:
    args = [a.strip() for a in m.group(1).split(",") if a.strip()]
    print("  args:")
    for a in args:
        print("    ", a[:110])
    print("  returns:", m.group(2))
    where = next((a for a in args if a.startswith("where")), "")
    wt = where.split(":")[-1].strip().rstrip("!") if where else None
else:
    print("  not found in SDL")
    wt = None

for label, tname, kw in (("filter input", wt, "input"),
                         ("node type", "MAT_Jordstykke", "type")):
    if not tname:
        continue
    b = block(tname, kw)
    print(f"\n=== {label}: {tname} ===")
    if b is None:
        print("  not found")
        continue
    fs = fields_of(b)
    print(f"  {len(fs)} fields")
    for n, t in fs:
        print(f"    {n:<38} {t}")

print("\n=== geometry candidates across the whole schema ===")
geom_types = sorted(set(re.findall(r":\s*(\w*(?:Geometri|Geometry|Polygon|Flade|GML|WKT)\w*)", clean)))
print("  types with geometric names:", geom_types[:20] or "none")
b = block("MAT_Jordstykke", "type")
if b:
    hits = [(n, t) for n, t in fields_of(b)
            if re.search(r"geom|flade|polygon|wkt|gml|koordinat|shape", n + t, re.I)]
    print("  geometry-ish on MAT_Jordstykke:", hits or "NONE")

print("\nDelete this script and its workflow once read.")
