#!/usr/bin/env python3
"""Temporary probe #2: is the 403 from Datafordeler "we don't know you" or "you
aren't subscribed to this service"?

Probe #1 got 403 "Unauthorized access" on BBR, Ejendomsvurdering and EBR under
both auth styles, which is ambiguous. This discriminates by sending deliberately
wrong credentials alongside the real ones:

  wrong -> 401 and real -> 403   => the account is recognised, it just lacks the
                                    service subscription (fix: self-service)
  wrong -> 403 and real -> 403   => the message is generic; more likely the user
                                    type or auth scheme is wrong

Also tries HTTP Basic, since Datafordeler documents more than one scheme.
Credentials never reach stdout; every URL is redacted before logging.
"""
import base64
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

USER = os.environ.get("DATAFORDELER_USER", "").strip()
PASS = os.environ.get("DATAFORDELER_PASS", "").strip()
APIK = os.environ.get("DATAFORDELER_API", "").strip()
SECRETS = [s for s in (USER, PASS, APIK) if s]


def redact(text):
    out = str(text)
    for s in SECRETS:
        out = out.replace(s, "***").replace(urllib.parse.quote(s, safe=""), "***")
    return out


if not (USER and PASS):
    sys.exit("need DATAFORDELER_USER and DATAFORDELER_PASS")

DF = "https://services.datafordeler.dk"
BBR = f"{DF}/BBR/BBRPublic/1/rest/bygning"
UA = {"User-Agent": "bolig-tracker/1.0 (+https://boligtracker.dk)", "Accept": "application/json"}


def go(label, url, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read(400).decode("utf-8", "replace")
            print(f"  {r.status:>3}  {label}")
            print(f"        {redact(body)[:160]}")
            return r.status
    except urllib.error.HTTPError as ex:
        body = ""
        try:
            body = ex.read(300).decode("utf-8", "replace")
        except Exception:
            pass
        print(f"  {ex.code:>3}  {label}")
        if body:
            print(f"        {redact(body)[:160]}")
        return ex.code
    except Exception as ex:
        print(f"  ---  {label}  ({type(ex).__name__}: {redact(str(ex))[:70]})")
        return None


q = {"Kommunekode": "0223", "pagesize": "1"}

print("=== does Datafordeler recognise the account at all? ===")
real = go("real credentials, query params",
          BBR + "?" + urllib.parse.urlencode({**q, "username": USER, "password": PASS}))
fake = go("deliberately wrong credentials",
          BBR + "?" + urllib.parse.urlencode({**q, "username": "no_such_user_xyz",
                                              "password": "definitely_wrong_xyz"}))
none = go("no credentials at all", BBR + "?" + urllib.parse.urlencode(q))

print("\n=== other auth schemes with the real account ===")
tok = base64.b64encode(f"{USER}:{PASS}".encode()).decode()
go("HTTP Basic", BBR + "?" + urllib.parse.urlencode(q), {"Authorization": f"Basic {tok}"})
if APIK:
    go("api key as header X-Api-Key", BBR + "?" + urllib.parse.urlencode(q),
       {"X-Api-Key": APIK})
    go("api key as ?token=", BBR + "?" + urllib.parse.urlencode({**q, "token": APIK}))

print("\n=== is the API key a Dataforsyningen (DAWA/kort) key instead? ===")
if APIK:
    go("Dataforsyningen DHM with token",
       "https://api.dataforsyningen.dk/DHMTerraen?" + urllib.parse.urlencode(
           {"geop": "12.561188,55.86308", "elevationmodel": "dtm", "token": APIK}))

print("\n=== verdict ===")
if real == 403 and fake in (401, 400):
    print("  Account IS recognised. 403 = missing service subscription.")
    print("  -> subscribe the user to BBR / Matriklen / Ejendomsvurdering / EBR")
    print("     in Datafordeler self-service, then re-run.")
elif real == 403 and fake == 403:
    print("  Wrong credentials give the SAME 403, so the message is generic.")
    print("  -> most likely the user is not a 'tjenestebruger'/webbruger, or the")
    print("     service needs a different auth scheme. Check the user type.")
elif real == 200:
    print("  It works — BBR is readable.")
else:
    print(f"  Inconclusive: real={real} fake={fake} none={none}")

print("\nDelete this script and its workflow once read.")
