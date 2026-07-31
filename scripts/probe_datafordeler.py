#!/usr/bin/env python3
"""Temporary probe #3: find Datafordeler's GraphQL endpoint and the parameter
name the API key goes in.

Probes #1 and #2 aimed an API key at the REST services and got a flat 403 no
matter what. That was the wrong door: per Datafordeler's own login page, an
API key created under an IT-system in Administration is for *Fildownload and
GraphQL*, while REST belongs to the Webbruger/Tjenestebruger path — which is
itself being retired ultimo 2026.

So: try the plausible GraphQL URLs, with the key under the plausible parameter
names, and see which combination answers. Credentials never reach stdout.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

APIK = os.environ.get("DATAFORDELER_API", "").strip()
USER = os.environ.get("DATAFORDELER_USER", "").strip()
PASS = os.environ.get("DATAFORDELER_PASS", "").strip()
SECRETS = [s for s in (APIK, USER, PASS) if s]


def redact(t):
    out = str(t)
    for s in SECRETS:
        out = out.replace(s, "***").replace(urllib.parse.quote(s, safe=""), "***")
    return out


if not APIK:
    sys.exit("no DATAFORDELER_API in the environment")
print(f"api key present, length {len(APIK)}")

UA = {"User-Agent": "bolig-tracker/1.0 (+https://boligtracker.dk)"}
# smallest possible question: what is the query root called?
INTROSPECT = {"query": "{__schema{queryType{name}}}"}

ENDPOINTS = [
    "https://services.datafordeler.dk/GraphQL",
    "https://services.datafordeler.dk/graphql",
    "https://services.datafordeler.dk/DAR/DAR/1/GraphQL",
    "https://services.datafordeler.dk/DAR/DAR/3.0.0/GraphQL",
    "https://services.datafordeler.dk/BBR/BBRPublic/1/GraphQL",
    "https://graphql.datafordeler.dk/",
    "https://api.datafordeler.dk/graphql",
]
KEY_PARAMS = ["api_key", "apikey", "API_KEY", "key", "token"]


def post(url, payload, headers=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={**UA, "Content-Type": "application/json", "Accept": "application/json",
                 **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(700)
    except urllib.error.HTTPError as ex:
        body = b""
        try:
            body = ex.read(300)
        except Exception:
            pass
        return ex.code, body
    except Exception as ex:
        return None, f"{type(ex).__name__}: {str(ex)[:70]}".encode()


print("\n=== which endpoint exists at all? (no key) ===")
alive = []
for ep in ENDPOINTS:
    code, body = post(ep, INTROSPECT)
    snippet = redact(body[:110].decode("utf-8", "replace")).replace("\n", " ")
    print(f"  {str(code):>4}  {ep}")
    if snippet.strip():
        print(f"         {snippet}")
    if code and code != 404:
        alive.append(ep)

if not alive:
    print("\nNo GraphQL endpoint responded — the path is somewhere else.")
    sys.exit(0)

print(f"\n=== key placement, against {len(alive)} live endpoint(s) ===")
for ep in alive:
    for p in KEY_PARAMS:
        code, body = post(f"{ep}?{p}={urllib.parse.quote(APIK)}", INTROSPECT)
        mark = "  <-- WORKS" if code == 200 else ""
        print(f"  {str(code):>4}  ?{p}=  {ep}{mark}")
        if code == 200:
            print("         ", redact(body[:400].decode("utf-8", "replace")))
            break
    # header-based variants
    for hname in ("X-API-Key", "Authorization"):
        val = APIK if hname == "X-API-Key" else f"Bearer {APIK}"
        code, body = post(ep, INTROSPECT, {hname: val})
        mark = "  <-- WORKS" if code == 200 else ""
        print(f"  {str(code):>4}  hdr {hname:<14} {ep}{mark}")
        if code == 200:
            print("         ", redact(body[:400].decode("utf-8", "replace")))

print("\nDelete this script and its workflow once read.")
