#!/usr/bin/env python3
"""Temporary probe: MAT_Jordstykke's real fields, filters and geometry format.

The earlier schema scrape matched GraphQL *description* blocks rather than the
schema, so the parcel geometry field name is still unconfirmed. This uses real
introspection instead of a regex, then runs a live query to see what geometry
actually comes back on the wire.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

KEY = os.environ.get("DATAFORDELER_API", "").strip()
if not KEY:
    sys.exit("no DATAFORDELER_API")
EK = urllib.parse.quote(KEY, safe="")
URL = f"https://graphql.datafordeler.dk/MAT/v2?apiKey={EK}"
UA = {"User-Agent": "bolig-tracker/1.0", "Content-Type": "application/json",
      "Accept": "application/json"}
NOW = "2026-07-31T12:00:00Z"


def redact(t):
    return str(t).replace(KEY, "***").replace(urllib.parse.quote(KEY, safe=""), "***")


def gql(query, cap=60000):
    req = urllib.request.Request(URL, data=json.dumps({"query": query}).encode(), headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read(cap)
    except urllib.error.HTTPError as ex:
        try:
            return ex.code, ex.read(800)
        except Exception:
            return ex.code, b""
    except Exception as ex:
        return None, f"{type(ex).__name__}: {str(ex)[:80]}".encode()


def typestr(t):
    """Unwrap NON_NULL / LIST wrappers to a readable name."""
    if not t:
        return "?"
    if t.get("name"):
        return t["name"]
    inner = typestr(t.get("ofType"))
    return {"NON_NULL": f"{inner}!", "LIST": f"[{inner}]"}.get(t.get("kind"), inner)


FRAG = "name kind ofType { name kind ofType { name kind ofType { name kind } } }"

print("=== MAT_Jordstykke fields ===")
code, body = gql("{ __type(name: \"MAT_Jordstykke\") { fields { name type { %s } } } }" % FRAG)
print(f"  HTTP {code}")
fields = []
if code == 200:
    d = json.loads(body.decode("utf-8", "replace"))
    t = (d.get("data") or {}).get("__type") or {}
    for f in (t.get("fields") or []):
        fields.append((f["name"], typestr(f["type"])))
    for n, ty in fields:
        print(f"    {n:<34} {ty}")
else:
    print(" ", redact(body.decode("utf-8", "replace"))[:600])

geom = [n for n, _ in fields if any(k in n.lower() for k in
        ("geom", "flade", "polygon", "wkt", "shape", "koordinat"))]
print(f"\n  geometry-ish fields: {geom or 'NONE FOUND'}")

print("\n=== what can we filter on? ===")
code, body = gql("{ __type(name: \"MAT_JordstykkeFilterInput\") { inputFields { name type { %s } } } }" % FRAG)
if code == 200:
    d = json.loads(body.decode("utf-8", "replace"))
    t = (d.get("data") or {}).get("__type") or {}
    names = [f["name"] for f in (t.get("inputFields") or [])]
    print(f"  {len(names)} filters: {names[:40]}")
    spatial = [n for n in names if any(k in n.lower() for k in
               ("geom", "within", "intersect", "bbox", "flade", "distance"))]
    print(f"  spatial filters: {spatial or 'none — filter by kommune/ejerlav instead'}")
else:
    print(f"  HTTP {code}", redact(body.decode("utf-8", "replace"))[:300])

print("\n=== live sample: one parcel in Hørsholm ===")
sel = " ".join(n for n, _ in fields
               if n in ("id_lokalId", "matrikelnummer", "ejerlavLokalId", "kommunekode",
                        "registreretAreal", "vejareal", "vandarealBeregningsmetode",
                        "bfeNummer", "jordstykkeAdresse") or n in geom)
if not sel:
    sel = "id_lokalId"
q = """query {
  MAT_Jordstykke(
    first: 1
    registreringstid: "%s"
    virkningstid: "%s"
    where: { kommunekode: { eq: "0223" } }
  ) { nodes { %s } }
}""" % (NOW, NOW, sel)
print("  selecting:", sel[:200])
code, body = gql(q, 20000)
print(f"  HTTP {code}")
print(" ", redact(body.decode("utf-8", "replace"))[:2500])

print("\nDelete this script and its workflow once read.")
