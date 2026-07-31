#!/usr/bin/env python3
"""Temporary probe #4: does DATAFORDELER_USERID + the API key unlock anything?

Probe #3 found the GraphQL host — graphql.datafordeler.dk answers 401, not 404 —
but no single-value key placement was accepted. Datafordeler's API keys belong
to an IT-system, so an ID/key *pair* is the obvious missing half. This tries the
pair across both GraphQL and REST, in the shapes such APIs usually take.

Credentials never reach stdout; URLs are redacted before logging.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

UID = os.environ.get("DATAFORDELER_USERID", "").strip()
APIK = os.environ.get("DATAFORDELER_API", "").strip()
USER = os.environ.get("DATAFORDELER_USER", "").strip()
PASS = os.environ.get("DATAFORDELER_PASS", "").strip()
SECRETS = [s for s in (UID, APIK, USER, PASS) if s]


def redact(t):
    out = str(t)
    for s in SECRETS:
        out = out.replace(s, "***").replace(urllib.parse.quote(s, safe=""), "***")
    return out


print("secrets present:", f"userid={'yes' if UID else 'NO'}",
      f"api={'yes' if APIK else 'NO'}", f"user={'yes' if USER else 'NO'}",
      f"pass={'yes' if PASS else 'NO'}")
if not (UID and APIK):
    sys.exit("need DATAFORDELER_USERID and DATAFORDELER_API")
print(f"userid length {len(UID)}, api key length {len(APIK)}")

UA = {"User-Agent": "bolig-tracker/1.0 (+https://boligtracker.dk)"}
GQL = ["https://graphql.datafordeler.dk/", "https://api.datafordeler.dk/graphql"]
BBR = "https://services.datafordeler.dk/BBR/BBRPublic/1/rest/bygning"
INTRO = {"query": "{__schema{queryType{name}}}"}
BBRQ = {"Kommunekode": "0223", "pagesize": "1"}


def hit(label, url, payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    h = {**UA, "Accept": "application/json", **(headers or {})}
    if data:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read(600).decode("utf-8", "replace")
            print(f"  {r.status:>3}  {label}   <-- WORKS")
            print(f"        {redact(body)[:400]}")
            return r.status
    except urllib.error.HTTPError as ex:
        snippet = ""
        try:
            snippet = redact(ex.read(160).decode("utf-8", "replace")).replace("\n", " ")
        except Exception:
            pass
        print(f"  {ex.code:>3}  {label}" + (f"   {snippet[:90]}" if snippet else ""))
        return ex.code
    except Exception as ex:
        print(f"  ---  {label}  ({type(ex).__name__}: {redact(str(ex))[:60]})")
        return None


eu, ek = urllib.parse.quote(UID, safe=""), urllib.parse.quote(APIK, safe="")
basic = base64.b64encode(f"{UID}:{APIK}".encode()).decode()

print("\n=== GraphQL, id + key as a pair ===")
for ep in GQL:
    host = urllib.parse.urlparse(ep).netloc
    hit(f"{host} ?username=&password=", f"{ep}?username={eu}&password={ek}", INTRO)
    hit(f"{host} ?userid=&api_key=", f"{ep}?userid={eu}&api_key={ek}", INTRO)
    hit(f"{host} ?client_id=&client_secret=", f"{ep}?client_id={eu}&client_secret={ek}", INTRO)
    hit(f"{host} Basic id:key", ep, INTRO, {"Authorization": f"Basic {basic}"})
    hit(f"{host} X-Client-Id + X-API-Key", ep, INTRO,
        {"X-Client-Id": UID, "X-API-Key": APIK})
    hit(f"{host} Bearer key + X-User-Id", ep, INTRO,
        {"Authorization": f"Bearer {APIK}", "X-User-Id": UID})

print("\n=== REST BBR, in case the pair works the old way ===")
q = urllib.parse.urlencode(BBRQ)
hit("BBR ?username=<userid>&password=<apikey>", f"{BBR}?{q}&username={eu}&password={ek}")
hit("BBR ?username=<user>&password=<apikey>",
    f"{BBR}?{q}&username={urllib.parse.quote(USER, safe='')}&password={ek}" if USER else BBR)
hit("BBR Basic userid:apikey", f"{BBR}?{q}", None, {"Authorization": f"Basic {basic}"})

print("\nDelete this script and its workflow once read.")
