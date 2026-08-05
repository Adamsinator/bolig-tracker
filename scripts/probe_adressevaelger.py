#!/usr/bin/env python3
"""Diagnostic-only, for issue #28 (DAWA sunsets 17 Aug 2026 — ~12 days out).
Adressevælgeren (Klimadatastyrelsen's official DAWA replacement) requires a
`token` query param per its own bundled widget source (dist/adressevaelger.
iife.js: `soeg?tekst=${t}&token=${this.token}`). The only documented way to
get one points to a Confluence page (confluence.sdfi.dk) that 403s to
anonymous fetches, and a raw request from this environment to
adressevaelger.dk itself also 403'd via one fetch path — this checks
whether that's a real auth requirement or just that one client being
blocked, and what the actual error body/headers say.

Deleted once its findings are captured, per this repo's probe convention.
"""
import json
import urllib.error
import urllib.request

UA = {"Accept": "application/json, text/plain, */*",
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"}


def get(url, headers=None):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")[:1500]
    except urllib.error.HTTPError as ex:
        body = ""
        try:
            body = ex.read().decode("utf-8", "replace")[:1500]
        except Exception:
            pass
        return ex.code, dict(ex.headers or {}), body
    except Exception as ex:
        return None, {}, str(ex)


def main():
    print("1) soeg endpoint, no token at all")
    status, headers, body = get("https://adressevaelger.dk/soeg?tekst=Sofievej")
    print(f"   status={status}")
    print(f"   headers={json.dumps(headers, indent=2)[:800]}")
    print(f"   body={body}")

    print("\n2) soeg endpoint, obviously-fake token")
    status, headers, body = get("https://adressevaelger.dk/soeg?tekst=Sofievej&token=obviously-fake-token-12345")
    print(f"   status={status}")
    print(f"   body={body}")

    print("\n3) bare domain root")
    status, headers, body = get("https://adressevaelger.dk/")
    print(f"   status={status}")
    print(f"   headers={json.dumps(headers, indent=2)[:800]}")
    print(f"   body={body}")

    print("\n4) CORS preflight-style check: does a real browser Origin get an ACAO header back?")
    status, headers, body = get("https://adressevaelger.dk/soeg?tekst=Sofievej",
                                 headers={"Origin": "https://boligtracker.dk", "Referer": "https://boligtracker.dk/"})
    print(f"   status={status}")
    acao = headers.get("Access-Control-Allow-Origin") or headers.get("access-control-allow-origin")
    print(f"   Access-Control-Allow-Origin={acao!r}")
    print(f"   body={body}")

    print("\n5) does dataforsyningen.dk's self-service key portal (used for DATAFORDELER_API "
          "already) mention Adressevælger tokens at all?")
    status, headers, body = get("https://selfservice.dataforsyningen.dk/")
    print(f"   status={status}")
    print(f"   body={body[:600]}")

    print("\ndone")


if __name__ == "__main__":
    main()
