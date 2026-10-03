#!/usr/bin/env python3
"""Smoke check: 13 servisni real endpointda tekshir. Hammasi 200 -> exit 0."""
import sys
import urllib.request

TIMEOUT = 5
TARGETS = [
    (8090, "/api"),
    (8001, "/health"),
    (8022, "/api/health"),
    (8023, "/api/projects"),
    (8004, "/api/products"),
    (8005, "/api/health"),
    (8006, "/api/health"),
    (8007, "/api/health"),
    (8008, "/api/ai/prompts"),
    (8009, "/api/health"),
    (8010, "/api/news"),
    (8011, "/api/health"),
    (8012, "/api/health"),
]


def check(port, path):
    url = f"http://localhost:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            ok = r.status == 200
            print(f"{port}{path} {'OK' if ok else 'FAIL'} ({r.status})")
            return ok
    except Exception as e:
        print(f"{port}{path} FAIL ({e})")
        return False


def main():
    results = [check(p, u) for p, u in TARGETS]
    print(f"{sum(results)}/{len(TARGETS)} up")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
