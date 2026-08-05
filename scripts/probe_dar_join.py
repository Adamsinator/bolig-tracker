#!/usr/bin/env python3
"""Last probe before writing fetch_dar_addresses_once.py. Everything else
is confirmed: DAR_Husnummer/DAR_Adresse fields, DAR_Postnummer join,
DAR_Adressepunkt.position.wkt, and DAR_NavngivenVejKommunedel filters
cleanly by kommune. The one missing piece: how to actually narrow
DAR_Husnummer down to a specific kommune's streets. DAR_Husnummer has no
kommunekode/navngivenVejKommunedel field of its own (only `navngivenVej`,
a single street UUID) — house numbers don't span kommuner even when a
street does, so there may be a direct relation, or the only way in may be
`where: { navngivenVej: { in: [...] } }` using the street-ID set collected
from DAR_NavngivenVejKommunedel per kommune.

Real navngivenVej ids for kommune 0223 (Hoersholm), from the earlier
DAR_NavngivenVejKommunedel probe: 185749ce-9527-4913-acfe-417eb5b9834f,
9f64288a-1131-402b-adf7-cfbf276fd38e, 7a597f49-5983-4eef-bb5e-78a5e138efc2

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
VEJ_IDS = ["185749ce-9527-4913-acfe-417eb5b9834f",
           "9f64288a-1131-402b-adf7-cfbf276fd38e",
           "7a597f49-5983-4eef-bb5e-78a5e138efc2"]


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
    print(f"  {json.dumps(data, ensure_ascii=False, indent=2)[:1800]}")
    print()


def main():
    vej_list = ", ".join(f'"{v}"' for v in VEJ_IDS)

    run("1) DAR_Husnummer filtered by navngivenVej: { in: [...] } using 3 real Hoersholm street ids",
        f"""{{ DAR_Husnummer(first: 5, where: {{ navngivenVej: {{ in: [{vej_list}] }} }}
              registreringstid: "{NOW}" virkningstid: "{NOW}") {{
          pageInfo {{ hasNextPage endCursor }}
          nodes {{ id_lokalId husnummertekst adgangsadressebetegnelse navngivenVej }}
        }} }}""")

    run("2) does DAR_Husnummer have its own direct kommune-ish relation field? "
        "(probing field names, not filtering)",
        f"""{{ DAR_Husnummer(first: 1
              registreringstid: "{NOW}" virkningstid: "{NOW}") {{
          nodes {{ id_lokalId navngivenVejKommunedel husnummerKommunedel kommunedel }}
        }} }}""")

    print("done")


if __name__ == "__main__":
    main()
