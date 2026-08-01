#!/usr/bin/env python3
"""Temporary probe: discover MAT_Jordstykke's real argument and node types.

The previous version printed the response body only on non-200. GraphQL answers
200 with an `errors` block, so every real error was swallowed and the probe
reported "NONE FOUND" when it had actually been told what was wrong. Bodies are
now printed unconditionally.

What we know: `MAT_Jordstykke` is a valid query-root field (a GraphQL error came
back with path ["MAT_Jordstykke"]), but `kommunekode` is not a valid filter, and
__type(name: "MAT_Jordstykke") returned nothing — so the *type* names differ from
the field name. Walk the schema instead of guessing: query root -> the field's
arg types and return type -> the connection's node type -> its fields.
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
T = "name kind ofType { name kind ofType { name kind ofType { name kind } } }"


def redact(t):
    return str(t).replace(KEY, "***").replace(urllib.parse.quote(KEY, safe=""), "***")


def gql(query, cap=120000):
    req = urllib.request.Request(URL, data=json.dumps({"query": query}).encode(), headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read(cap)
    except urllib.error.HTTPError as ex:
        try:
            return ex.code, ex.read(900)
        except Exception:
            return ex.code, b""
    except Exception as ex:
        return None, f"{type(ex).__name__}: {str(ex)[:80]}".encode()


def call(query, cap=120000):
    """Always surface errors — that was the bug in the last probe."""
    code, body = gql(query, cap)
    txt = body.decode("utf-8", "replace")
    try:
        d = json.loads(txt)
    except Exception:
        print(f"    HTTP {code}, unparseable: {redact(txt)[:300]}")
        return None
    if d.get("errors"):
        print(f"    HTTP {code} GraphQL errors: "
              f"{redact(json.dumps([e.get('message') for e in d['errors']]))[:400]}")
    return d.get("data")


def unwrap(t):
    while t and not t.get("name"):
        t = t.get("ofType")
    return (t or {}).get("name")


def typestr(t):
    if not t:
        return "?"
    if t.get("name"):
        return t["name"]
    inner = typestr(t.get("ofType"))
    return {"NON_NULL": f"{inner}!", "LIST": f"[{inner}]"}.get(t.get("kind"), inner)


print("=== query root: the MAT_Jordstykke field ===")
d = call("{ __schema { queryType { fields { name args { name type { %s } } type { %s } } } } }" % (T, T))
field = None
if d:
    fields = ((d.get("__schema") or {}).get("queryType") or {}).get("fields") or []
    print(f"  {len(fields)} root fields")
    field = next((f for f in fields if f["name"] == "MAT_Jordstykke"), None)
    jrelated = [f["name"] for f in fields if "jordstykke" in f["name"].lower()]
    print(f"  jordstykke-ish roots: {jrelated[:10]}")
if not field:
    sys.exit("could not find the MAT_Jordstykke root field")

print("\n  args:")
where_type = None
for a in field["args"]:
    ts = typestr(a["type"])
    print(f"    {a['name']:<20} {ts}")
    if a["name"] == "where":
        where_type = unwrap(a["type"])
ret = unwrap(field["type"])
print(f"  returns: {typestr(field['type'])}   (unwrapped: {ret})")

if where_type:
    print(f"\n=== filter input: {where_type} ===")
    d = call('{ __type(name: "%s") { inputFields { name type { %s } } } }' % (where_type, T))
    ifs = ((d or {}).get("__type") or {}).get("inputFields") or []
    print(f"  {len(ifs)} filters: {[f['name'] for f in ifs][:40]}")

node_type = None
if ret:
    print(f"\n=== connection type: {ret} ===")
    d = call('{ __type(name: "%s") { fields { name type { %s } } } }' % (ret, T))
    cf = ((d or {}).get("__type") or {}).get("fields") or []
    print(f"  fields: {[f['name'] for f in cf]}")
    nodes = next((f for f in cf if f["name"] == "nodes"), None)
    if nodes:
        node_type = unwrap(nodes["type"])
        print(f"  nodes -> {typestr(nodes['type'])}   (unwrapped: {node_type})")

if node_type:
    print(f"\n=== node type: {node_type} ===")
    d = call('{ __type(name: "%s") { fields { name type { %s } } } }' % (node_type, T))
    nf = ((d or {}).get("__type") or {}).get("fields") or []
    for f in nf:
        print(f"    {f['name']:<36} {typestr(f['type'])}")
    geom = [f["name"] for f in nf if any(k in f["name"].lower() for k in
            ("geom", "flade", "polygon", "wkt", "shape", "koordinat", "gml"))]
    print(f"\n  geometry-ish: {geom or 'NONE'}")

print("\nDelete this script and its workflow once read.")
