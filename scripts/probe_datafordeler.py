#!/usr/bin/env python3
"""Temporary probe: which Datafordeler services is our user actually subscribed
to, and what do they return?

An account existing is not the same as an account being subscribed — every
service is granted separately — so this establishes what we can really read
before any of it gets designed into the build.

Credentials come from the environment and are never printed. Every URL is
redacted before it reaches a log line, because Datafordeler takes the username
and password as query parameters and this repo is public.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

USER = os.environ.get("DATAFORDELER_USER", "").strip()
PASS = os.environ.get("DATAFORDELER_PASS", "").strip()
APIK = os.environ.get("DATAFORDELER_API", "").strip()

SECRETS = [s for s in (USER, PASS, APIK) if s]


def redact(text):
    """Never let a credential reach stdout, even inside an exception message."""
    out = str(text)
    for s in SECRETS:
        if s:
            out = out.replace(s, "***")
            out = out.replace(urllib.parse.quote(s, safe=""), "***")
    return out


print("credentials present:",
      f"user={'yes' if USER else 'NO'}",
      f"pass={'yes' if PASS else 'NO'}",
      f"api={'yes' if APIK else 'NO'}")
if not (USER and PASS) and not APIK:
    sys.exit("no credentials in the environment — check the secret names")

UA = {"User-Agent": "bolig-tracker/1.0 (+https://boligtracker.dk)",
      "Accept": "application/json"}


def call(label, base, params, auth):
    """auth: 'userpass' | 'apikey' | 'none'"""
    p = dict(params)
    if auth == "userpass":
        p.update(username=USER, password=PASS)
    elif auth == "apikey":
        p.update(apikey=APIK)
    url = base + "?" + urllib.parse.urlencode(p)
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read(3000)
            ct = (r.headers.get("Content-Type") or "?").split(";")[0]
            print(f"  {r.status:>3} {auth:<9} {ct:<20} {label}")
            return r.status, body
    except urllib.error.HTTPError as ex:
        body = b""
        try:
            body = ex.read(400)
        except Exception:
            pass
        hint = {401: " <- not authenticated", 403: " <- not subscribed?",
                404: " <- wrong path"}.get(ex.code, "")
        print(f"  {ex.code:>3} {auth:<9} {'':<20} {label}{hint}")
        if body:
            print("        ", redact(body[:200].decode("utf-8", "replace")).replace("\n", " "))
        return ex.code, body
    except Exception as ex:
        print(f"  --- {auth:<9} {'':<20} {label}  ({type(ex).__name__}: "
              f"{redact(str(ex))[:80]})")
        return None, b""


DF = "https://services.datafordeler.dk"
# Hørsholm; Sofievej 11 sits in matrikel 10e, Smidstrup By, Rungsted (ejerlav 130752)
KOMMUNE, EJERLAV, MATRNR = "0223", "130752", "10e"

print("\n=== BBR (building register) ===")
for auth in ("userpass", "apikey"):
    call("BBR bygning by kommune", f"{DF}/BBR/BBRPublic/1/rest/bygning",
         {"Kommunekode": KOMMUNE, "pagesize": "1"}, auth)
    call("BBR grund", f"{DF}/BBR/BBRPublic/1/rest/grund",
         {"Kommunekode": KOMMUNE, "pagesize": "1"}, auth)
    call("BBR enhed", f"{DF}/BBR/BBRPublic/1/rest/enhed",
         {"Kommunekode": KOMMUNE, "pagesize": "1"}, auth)

print("\n=== Matriklen ===")
for auth in ("userpass", "apikey"):
    call("Matrikel jordstykke (REST)", f"{DF}/MATRIKLEN2/MATRIKLEN/1/REST/SamletFastEjendom",
         {"Ejerlavskode": EJERLAV, "Matrikelnummer": MATRNR}, auth)
    call("Matrikel WFS capabilities",
         f"{DF}/MATRIKLEN2/MatrikelGaeldendeOgForeloebigWFS/1.0.0/WFS",
         {"service": "WFS", "request": "GetCapabilities"}, auth)

print("\n=== Ejendomsvurdering + EBR ===")
for auth in ("userpass", "apikey"):
    call("Ejendomsvurdering by BFE", f"{DF}/EJENDOMSVURDERING/Ejendomsvurdering/1/REST/"
         "HentEjendomsvurderingerForBFE", {"BFEnummer": "2001642"}, auth)
    call("EBR ejendomsbeliggenhed", f"{DF}/EBR/Ejendomsbeliggenhed/1/REST/Ejendomsbeliggenhed",
         {"Kommunekode": KOMMUNE, "pagesize": "1"}, auth)

print("\n=== DAR (addresses — DAWA's successor path) ===")
for auth in ("userpass", "none"):
    call("DAR husnummer", f"{DF}/DAR/DAR/3.0.0/REST/husnummer",
         {"kommunekode": KOMMUNE, "pagesize": "1"}, auth)

# Show the shape of whatever worked, so the real implementation has something
# concrete to target.
print("\n=== payload shape of the first BBR call that succeeds ===")
for auth in ("userpass", "apikey"):
    code, body = call("BBR bygning (detail)", f"{DF}/BBR/BBRPublic/1/rest/bygning",
                      {"Kommunekode": KOMMUNE, "pagesize": "1"}, auth)
    if code == 200 and body:
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except Exception:
            print("   not json:", redact(body[:300].decode("utf-8", "replace")))
            break
        rec = data[0] if isinstance(data, list) and data else data
        if isinstance(rec, dict):
            print("   fields:", json.dumps(sorted(rec)[:40], ensure_ascii=False))
            keep = {k: rec[k] for k in list(rec)[:12]}
            print("   sample:", redact(json.dumps(keep, ensure_ascii=False))[:900])
        break

print("\nDone. Delete this script and its workflow once read.")
