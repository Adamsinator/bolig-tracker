#!/usr/bin/env python3
"""Diagnostic-only: does Boliga's API support looking up a SPECIFIC address's
own sale history (what fetch_sold() does today is kommune x type aggregates
and anonymised comp points, nothing keyed by address)? Boliga's own website
clearly shows per-address "prishistorik" (price history) when you search a
specific address, so some endpoint must support this — this checks whether
the existing sold-search endpoint accepts an address/street filter, and
looks for a distinct address/case-history endpoint.

Deleted once its findings are captured, per this repo's probe convention.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

UA = {"Accept": "application/json",
      "User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"}
BOLIGA_SOLD = "https://api.boliga.dk/api/v2/sold/search/results"


def get(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as ex:
        body = ""
        try:
            body = ex.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        return ex.code, body
    except Exception as ex:
        return None, str(ex)


def main():
    # a real Hørsholm address referenced earlier this session's survey (#26)
    print("1) does sold/search/results accept a free-text address/street param?")
    for param in ("street", "streetName", "address", "q", "search"):
        qs = urllib.parse.urlencode({"municipality": 223, "propertyType": 1, param: "Sofievej"})
        status, data = get(f"{BOLIGA_SOLD}?{qs}")
        n = len(data.get("results", [])) if isinstance(data, dict) else "?"
        print(f"   {param}=Sofievej -> status={status}, results={n}")

    print("\n1b) 'street' looked real (6 results, distinct from the 50-default the others gave). "
          "Does adding a house-number param narrow it to ONE address? Also: no municipality/type "
          "filter at all, just street+number — does that alone find the address anywhere?")
    for extra_params in (
        {"municipality": 223, "propertyType": 1, "street": "Sofievej", "number": "11"},
        {"municipality": 223, "propertyType": 1, "street": "Sofievej", "houseNumber": "11"},
        {"municipality": 223, "propertyType": 1, "street": "Sofievej", "streetNumber": "11"},
        {"street": "Sofievej", "houseNumber": "11"},
    ):
        qs = urllib.parse.urlencode(extra_params)
        status, data = get(f"{BOLIGA_SOLD}?{qs}")
        n = len(data.get("results", [])) if isinstance(data, dict) else "?"
        sample = ""
        if isinstance(data, dict) and data.get("results"):
            r0 = data["results"][0]
            sample = f" | first result address fields: street={r0.get('street')!r} houseNumber={r0.get('houseNumber')!r} floor={r0.get('floor')!r}"
        print(f"   {extra_params} -> status={status}, results={n}{sample}")

    print("\n2) does a dedicated case/address-history endpoint exist under api.boliga.dk?")
    for path in ("api/v2/case/history", "api/v2/address/history", "api/v2/homes/history",
                 "api/v2/sold/address", "api/v2/property/history"):
        status, data = get(f"https://api.boliga.dk/{path}?street=Sofievej&number=11")
        print(f"   /{path} -> status={status}, body[:150]={str(data)[:150]}")

    print("\n3) does the CURRENT-listing search (boligsiden, already used for listings.json) "
          "genuinely filter by street, or just ignore the param like Boliga's streetName/q/search "
          "did? Compare against an unfiltered baseline.")
    status, base = get("https://api.boligsiden.dk/search/cases?per_page=1")
    base_n = len(base.get("cases", [])) if isinstance(base, dict) else "?"
    status2, data = get("https://api.boligsiden.dk/search/cases?street=Sofievej&per_page=5")
    n2 = len(data.get("cases", [])) if isinstance(data, dict) else "?"
    streets = [c.get("address", {}).get("roadName") for c in data.get("cases", [])] if isinstance(data, dict) else None
    print(f"   baseline (no filter, per_page=1) -> {base_n} case(s)")
    print(f"   street=Sofievej (per_page=5) -> status={status2}, {n2} case(s), roadNames={streets}")

    print("\ndone")


if __name__ == "__main__":
    main()
