#!/usr/bin/env python3
"""Round 2, issue #28. Round 1 found: adressevaelger.dk's `token` query param
is checked for PRESENCE only, not validated as a real registered value (an
obviously-fake token got a normal 200 response), and CORS is wide open
(access-control-allow-origin: *) — same shape as DAWA itself.

This round checks whether the API can actually replace what the two live
DAWA integrations need:
  - setupGeo() (app.js) needs: a full address resolved to lat/lon.
  - setupLookup() (modelpage.js) needs: unit-level results (floor/door, e.g.
    "1. TV"), and IDs that join to our existing bbr_lookup.json the same way
    DAWA's adresse.id / adgangsadresseid did (confirmed compatible with
    DAR_Adresse.id_lokalId in an earlier probe this session).

Round 1's "Sofievej" query returned navngivenvejpostnummer results (street+
postnr, a disambiguation step) — this checks what a specific house-number
query and a detail-by-id lookup actually return.

Deleted once its findings are captured, per this repo's probe convention.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

UA = {"Accept": "application/json, text/plain, */*",
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"}
TOKEN = "adressevaelger123"   # the exact placeholder from the widget's own docs


def get(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as ex:
        body = ""
        try:
            body = ex.read().decode("utf-8", "replace")
        except Exception:
            pass
        return ex.code, body
    except Exception as ex:
        return None, str(ex)


def main():
    print("1) a specific full address incl. house number + unit (Esthersvej 45, 2900 Hellerup — "
          "the same test address used earlier this session for DAWA's unit-level results)")
    qs = urllib.parse.urlencode({"tekst": "Esthersvej 45, 2900 Hellerup", "token": TOKEN})
    status, body = get(f"https://adressevaelger.dk/soeg?{qs}")
    print(f"   status={status}")
    print(f"   body={body[:2500]}")

    print("\n2) same address without postnr, fewer chars — does it still disambiguate down to units?")
    qs = urllib.parse.urlencode({"tekst": "Esthersvej 45", "token": TOKEN})
    status, body = get(f"https://adressevaelger.dk/soeg?{qs}")
    print(f"   status={status}")
    print(f"   body={body[:2000]}")

    print("\n3) type-specific search — the widget's source showed both soeg (search-as-you-type) "
          "and /{type}/{id} (detail lookup). Try the 'adgangsadresse'/'adresse' type param seen "
          "in DAWA's own vocabulary, in case this API reuses it.")
    for t in ("adresse", "adgangsadresse"):
        qs = urllib.parse.urlencode({"tekst": "Esthersvej 45, 2900", "token": TOKEN, "type": t})
        status, body = get(f"https://adressevaelger.dk/soeg?{qs}")
        print(f"   type={t} status={status} body={body[:600]}")

    print("\ndone")


if __name__ == "__main__":
    main()
