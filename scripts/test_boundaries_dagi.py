#!/usr/bin/env python3
"""One-off verification: run the real fetch_boundaries() (DAGI-primary) for
a few kommuner and compare against the committed data/geo.json (DAWA-
sourced, from the last daily build) — bbox and simplified point counts
should be close. Not part of the daily build; deleted once #27 ships."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import build_data  # noqa: E402

TEST_SLUGS = ["hoersholm", "koebenhavn", "gribskov", "dragoer"]


def main():
    build_data.MUNICIPALITIES = {
        k: v for k, v in build_data.MUNICIPALITIES.items() if k in TEST_SLUGS
    }
    print(f"DATAFORDELER_API set: {bool(build_data.DATAFORDELER_API_KEY)}")
    geo = build_data.fetch_boundaries()

    with open("data/geo.json", encoding="utf-8") as f:
        committed = json.load(f)

    for slug in TEST_SLUGS:
        new = geo.get(slug)
        old = committed.get(slug)
        print(f"\n=== {slug} ===")
        if not new:
            print("  FAILED to fetch")
            continue
        print(f"  new bbox: {new['bbox']}")
        if old:
            print(f"  old bbox: {old['bbox']}")
            db = [abs(a - b) for a, b in zip(new["bbox"], old["bbox"])]
            print(f"  bbox diff: {db}")
        print(f"  new rings={len(new['rings'])} pts={sum(len(r) for r in new['rings'])}")
        if old:
            print(f"  old rings={len(old['rings'])} pts={sum(len(r) for r in old['rings'])}")


if __name__ == "__main__":
    main()
