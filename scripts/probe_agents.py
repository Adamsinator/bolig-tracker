#!/usr/bin/env python3
"""Temporary probe: what do the estate agents' own sites say about automated
access?

robots.txt is the machine-readable answer to "may a crawler fetch this", and the
terms pages are the contractual one. This fetches both and reports them, so the
question gets decided on what they actually publish rather than on assumption.
"""
import re
import urllib.error
import urllib.parse
import urllib.request

SITES = ["https://www.nybolig.dk", "https://www.home.dk", "https://www.edc.dk",
         "https://www.danbolig.dk", "https://www.estate.dk",
         "https://www.realmaeglerne.dk", "https://www.paulun.dk"]

UA = {"User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/126.0.0.0 Safari/537.36"),
      "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
      "Accept-Language": "da,en;q=0.7"}

TERMS_HINT = re.compile(r"(vilk|betingels|terms|persondata|datapolitik|cookie|"
                        r"handelsbeting|ophavsret|copyright|brugsbeting)", re.I)


def get(url, cap=200000):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(cap).decode("utf-8", "replace")
    except urllib.error.HTTPError as ex:
        return ex.code, ""
    except Exception as ex:
        return None, f"{type(ex).__name__}: {str(ex)[:70]}"


for site in SITES:
    host = urllib.parse.urlparse(site).netloc
    print("\n" + "=" * 74)
    print(host)
    print("=" * 74)

    code, txt = get(site + "/robots.txt", 20000)
    print(f"  robots.txt -> {code}")
    if code == 200 and txt:
        lines = [l.rstrip() for l in txt.splitlines() if l.strip()]
        # the interesting parts: global rules and anything mentioning search/bots
        shown = 0
        for l in lines:
            if shown >= 30:
                print(f"      … {len(lines)-shown} more lines")
                break
            print("      " + l[:110])
            shown += 1
    elif txt:
        print("      " + txt[:120])

    code, html = get(site)
    print(f"  homepage   -> {code}")
    if code == 200 and html:
        hrefs = set()
        for m in re.findall(r'href=["\']([^"\']+)["\']', html):
            if TERMS_HINT.search(m):
                hrefs.add(urllib.parse.urljoin(site, m))
        if hrefs:
            print("  terms-ish links found:")
            for h in sorted(hrefs)[:12]:
                print("      " + h[:110])
        else:
            print("  no terms-ish links in the homepage HTML "
                  "(likely rendered client-side)")

print("\n" + "=" * 74)
print("robots.txt answers 'may a crawler fetch this'. The terms pages answer")
print("whether the DATA may be reused. Both matter; neither overrides the other.")
print("Delete this script and its workflow once read.")
