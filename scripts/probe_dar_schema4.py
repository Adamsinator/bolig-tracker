#!/usr/bin/env python3
"""Round 5 (last), issue #28. Everything else is now confirmed: DAR_Husnummer
fields, DAR_Adresse fields, DAR_Postnummer join (works), and
DAR_NavngivenVejKommunedel for region-scoping by kommune (works). The only
remaining unknown is `position`'s subfields on DAR_Adressepunkt — GraphQL
said it's a `SpatialPointEpsg25832Type` needing a subselection, but didn't
say what the subfields are called. Search results described it as WKT
("POINT (723930 6179643.37)"), suggesting a `wkt` field; trying the most
likely candidate names.

Deleted once its findings are captured, per this repo's probe convention.
"""
import json
import os
import time
import urllib.error
import urllib.request

API_KEY = os.environ["DATAFORDELER_API"]
UA = {"User-Agent": "bolig-tracker/1.0 (+https://github.com/Adamsinator/bolig-tracker)"}
DAR = "https://graphql.datafordeler.dk/DAR/v3"
NOW = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
SAMPLE_ADGANGSPUNKT = "d90e9338-0470-41bc-8ecb-6f71dd912ff0"


def post_graphql(query, timeout=25):
    body = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(f"{DAR}?apiKey={API_KEY}", data=body,
                                  headers={**UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as ex:
        body_txt = ""
        try:
            body_txt = ex.read().decode("utf-8", "replace")[:1500]
        except Exception:
            pass
        return ex.code, {"httpError": body_txt}
    except Exception as ex:
        return None, {"exception": f"{type(ex).__name__}: {ex}"}


def run(label, query):
    print(label)
    status, data = post_graphql(query)
    print(f"  status={status}")
    print(f"  {json.dumps(data, ensure_ascii=False, indent=2)[:2000]}")
    print()


def main():
    run("position subfield candidates: wkt, x, y, coordinates, srid",
        f"""{{ DAR_Adressepunkt(first: 1, where: {{ id_lokalId: {{ eq: "{SAMPLE_ADGANGSPUNKT}" }} }}
              registreringstid: "{NOW}" virkningstid: "{NOW}") {{
          nodes {{ id_lokalId position {{ wkt x y coordinates srid }} }}
        }} }}""")
    print("done")


if __name__ == "__main__":
    main()
