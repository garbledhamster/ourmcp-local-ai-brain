from __future__ import annotations

import sys

if "--help" in sys.argv:
    print("fake distill help")
    raise SystemExit(0)

body = sys.stdin.read().strip()
print("Summary: " + (body.splitlines()[0] if body else "empty"))
print("Tags: fake, test")
print("Search text: " + body[:500])
