#!/usr/bin/env python3
"""Temporary probe #5: Datafordeler GraphQL, with the documented shape.

Earlier probes had the host right and everything else wrong: the key parameter
is `apiKey` (camelCase) and the path carries the register and version, e.g.
    https://graphql.datafordeler.dk/BBR/v3?apiKey=...
    https://graphql.datafordeler.dk/BBR/v3/schema?apiKey=...

Queries are bitemporal — registreringstid and virkningstid are required — and
paginate with first/after against pageInfo.endCursor until hasNextPage is false.

Credentials never reach stdout; URLs are redacted before logging.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

KEY = os.environ.get("DATAFORDELER_API", "").strip()
if not KEY:
    sys.exit("no DATAFORDELER_API in the environment")
SECRETS = [KEY] + [v for v in (os.environ.get("DATAFORDELER_USERID", ""),
                               os.environ.get("DATAFORDELER_USER", ""),
                               os.environ.get("DATAFORDELER_PASS", "")) if v.strip()]


def redact(t):
    out = str(t)
    for s in SECRETS:
        if s:
            out = out.replace(s, "***").replace(urllib.parse.quote(s, safe=""), "***")
    return out


GQL = "https://graphql.datafordeler.dk"
EK = urllib.parse.quote(KEY, safe="")
UA = {"User-Agent": "bolig-tracker/1.0 (+https://boligtracker.dk)"}
# bitemporal "as of now" — both stamps are required
NOW = "2026-07-31T12:00:00Z"
KOMMUNE = "0223"          # Hørsholm, where Sofievej 11 sits


def get(url, cap=4000):
    req = urllib.request.Request(url, headers={**UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read(cap)
    except urllib.error.HTTPError as ex:
        try:
            return ex.code, ex.read(300)
        except Exception:
            return ex.code, b""
    except Exception as ex:
        return None, f"{type(ex).__name__}: {str(ex)[:70]}".encode()


def post(url, query, cap=4000):
    data = json.dumps({"query": query}).encode()
    req = urllib.request.Request(url, data=data, headers={
        **UA, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read(cap)
    except urllib.error.HTTPError as ex:
        try:
            return ex.code, ex.read(500)
        except Exception:
            return ex.code, b""
    except Exception as ex:
        return None, f"{type(ex).__name__}: {str(ex)[:70]}".encode()


print("=== schema endpoints ===")
for reg in ("BBR", "DAR", "MATRIKLEN", "EJENDOMSVURDERING"):
    code, body = get(f"{GQL}/{reg}/v3/schema?apiKey={EK}", 1500)
    txt = redact(body.decode("utf-8", "replace"))
    print(f"  {str(code):>4}  {reg}/v3/schema  ({len(body)} bytes read)")
    if code == 200:
        types = re.findall(r"type\s+(\w+)\s*\{", txt)
        if types:
            print(f"         types seen: {types[:8]}")
    elif txt.strip():
        print(f"         {txt[:150]}")

print("\n=== BBR_Bygning query for Hørsholm ===")
q = """query {
  BBR_Bygning(
    first: 3
    registreringstid: "%s"
    virkningstid: "%s"
    where: { kommunekode: { eq: "%s" } }
  ) {
    pageInfo { endCursor hasNextPage }
    nodes { kommunekode husnummer id_lokalId byg007Bygningsnummer grund }
  }
}""" % (NOW, NOW, KOMMUNE)
code, body = post(f"{GQL}/BBR/v3?apiKey={EK}", q)
print(f"  HTTP {code}")
print(" ", redact(body.decode("utf-8", "replace"))[:1400])

if code == 200:
    try:
        d = json.loads(body.decode("utf-8", "replace"))
        nodes = (((d.get("data") or {}).get("BBR_Bygning") or {}).get("nodes")) or []
        if nodes:
            print(f"\n  -> {len(nodes)} building(s). First husnummer id:",
                  redact(nodes[0].get("husnummer")))
            hn = nodes[0].get("husnummer")
            if hn:
                print("\n=== join through to DAR_Husnummer ===")
                q2 = """query {
  DAR_Husnummer(
    first: 1
    registreringstid: "%s"
    virkningstid: "%s"
    where: { id_lokalId: { eq: "%s" } }
  ) { nodes { id_lokalId husnummertekst } }
}""" % (NOW, NOW, hn)
                c2, b2 = post(f"{GQL}/DAR/v3?apiKey={EK}", q2)
                print(f"  HTTP {c2}")
                print(" ", redact(b2.decode("utf-8", "replace"))[:700])
    except Exception as ex:
        print("  could not parse:", redact(str(ex))[:120])

print("\nDelete this script and its workflow once read.")
