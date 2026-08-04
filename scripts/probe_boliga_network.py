#!/usr/bin/env python3
"""Diagnostic-only: boliga.dk's own website clearly shows per-address sale
history when you search a specific address (user-confirmed by direct
observation), even though every guessed REST path on api.boliga.dk
(case/history, address/history, homes/history, sold/address, property/history)
404'd in probe_boliga_address_history.py. Guessing endpoint names further is
a dead end — this instead drives a real browser against the live site and
records every JSON network call it makes while performing an address search,
so we can read off the *real* endpoint from actual traffic instead of guesses.

Round 1: reconnaissance. Load the homepage, log search-input DOM structure,
attempt a real address search + click into the first result, and dump every
JSON XHR/fetch response seen along the way (url + status + a body snippet).

Deleted once its findings are captured, per this repo's probe convention.
"""
from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        seen = []

        def on_response(resp):
            try:
                ct = resp.headers.get("content-type", "")
            except Exception:
                ct = ""
            if "json" not in ct:
                return
            try:
                body = resp.text()[:500]
            except Exception:
                body = "<unreadable>"
            seen.append((resp.status, resp.url, body))

        page.on("response", on_response)

        print("=== loading homepage ===")
        try:
            page.goto("https://www.boliga.dk/", timeout=30000, wait_until="networkidle")
        except Exception as ex:
            print("  goto networkidle failed, retrying with load only:", ex)
            page.goto("https://www.boliga.dk/", timeout=30000, wait_until="load")
        print("title:", page.title())
        print("url:", page.url)

        print("\n=== input elements on homepage (first 10) ===")
        els = page.query_selector_all("input")
        for el in els[:10]:
            try:
                print(f"  name={el.get_attribute('name')!r} type={el.get_attribute('type')!r} "
                      f"placeholder={el.get_attribute('placeholder')!r} id={el.get_attribute('id')!r} "
                      f"data-testid={el.get_attribute('data-testid')!r} "
                      f"aria-label={el.get_attribute('aria-label')!r}")
            except Exception as ex:
                print("  (error reading input)", ex)

        print("\n=== trying a real address search interaction ===")
        try:
            box = (page.query_selector("input[type=search]")
                   or page.query_selector("input[placeholder*=adresse i]")
                   or page.query_selector("input[aria-label*=søg i]")
                   or page.query_selector("input"))
            if box:
                box.click()
                box.type("Sofievej 11", delay=80)
                page.wait_for_timeout(2500)
                print("  typed 'Sofievej 11'")
                for sel in ["[role=option]", "li[class*=suggest i]", "[class*=suggest i]",
                            "[class*=option i]", "[class*=autocomplete i]", "a[href*=bolig]"]:
                    found = page.query_selector_all(sel)
                    if found:
                        txt = ""
                        try:
                            txt = found[0].inner_text()[:150]
                        except Exception:
                            pass
                        print(f"   candidate sel={sel!r} count={len(found)} first_text={txt!r}")
                cand = (page.query_selector_all("[role=option]")
                        or page.query_selector_all("li a[href*=bolig]")
                        or page.query_selector_all("a[href*=bolig]"))
                if cand:
                    href = None
                    try:
                        href = cand[0].get_attribute("href")
                    except Exception:
                        pass
                    print(f"  clicking first candidate, href={href!r}")
                    cand[0].click()
                    page.wait_for_timeout(3500)
                    print("  landed on:", page.url)
                else:
                    print("  no candidate suggestion found, pressing Enter instead")
                    box.press("Enter")
                    page.wait_for_timeout(3500)
                    print("  landed on:", page.url)
            else:
                print("  no input element found at all")
        except Exception as ex:
            print("  interaction failed:", ex)

        print(f"\n=== {len(seen)} JSON responses captured (last 40) ===")
        for status, url, body in seen[-40:]:
            print(f"  [{status}] {url}\n      {body}\n")

        browser.close()


if __name__ == "__main__":
    main()
