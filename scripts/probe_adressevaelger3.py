#!/usr/bin/env python3
"""Round 3, issue #28. Round 2's /soeg search for "Esthersvej 45, 2900
Hellerup" returned exactly ONE result at type=husnummer (house-number
level) — no lat/lon, no floor/door breakdown into individual units (DAWA
gave 9 for this same address: kl., st. th/tv, 1.-3. th/tv). The bundled
widget JS also calls a second pattern, `/${type}/${id}?token=...`, for
detail lookups. This checks whether that detail endpoint is where unit
listing + coordinates actually live.

Deleted once its findings are captured, per this repo's probe convention.
"""
import urllib.error
import urllib.parse
import urllib.request

UA = {"Accept": "application/json, text/plain, */*",
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"}
TOKEN = "adressevaelger123"
HUSNUMMER_ID = "0a3f507b-dc37-32b8-e044-0003ba298018"   # Esthersvej 45, 2900 Hellerup


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
    print("1) detail lookup: /husnummer/{id}?token=...")
    qs = urllib.parse.urlencode({"token": TOKEN})
    status, body = get(f"https://adressevaelger.dk/husnummer/{HUSNUMMER_ID}?{qs}")
    print(f"   status={status}")
    print(f"   body={body[:3000]}")

    print("\n2) detail lookup: /adgangsadresse/{id}?token=... (in case the widget's generic "
          "type slug differs from the search result's own 'type' field)")
    status, body = get(f"https://adressevaelger.dk/adgangsadresse/{HUSNUMMER_ID}?{qs}")
    print(f"   status={status}")
    print(f"   body={body[:1000]}")

    print("\n3) does /soeg return anything MORE specific if we ask for a known unit directly by text "
          "(e.g. '1. tv')?")
    for txt in ("Esthersvej 45, 1. tv, 2900 Hellerup", "Esthersvej 45, 1.tv", "Esthersvej 45 1 tv"):
        qs2 = urllib.parse.urlencode({"tekst": txt, "token": TOKEN})
        status, body = get(f"https://adressevaelger.dk/soeg?{qs2}")
        print(f"   tekst={txt!r} -> status={status} body={body[:600]}")

    print("\ndone")


if __name__ == "__main__":
    main()
