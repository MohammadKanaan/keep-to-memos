#!/usr/bin/env python3
"""Live idempotency + zip-input test: run the tool a second time against an
already-imported instance, using the zip fixture as source. Every note
should be SKIP (already imported); nothing should be created or fail.

Requires a running memos instance with an existing imported dataset (run
validate_live.py first). Configure via the same environment variables
(MEMOS_URL / MEMOS_USER / MEMOS_PASSWORD).

Usage: python3 tests/live/idempotency_live.py
Exit code 0 = second run was a no-op.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import urllib.request

BASE = os.environ.get("MEMOS_URL", "http://localhost:5230").rstrip("/")
USER = os.environ.get("MEMOS_USER", "admin")
PASSWORD = os.environ.get("MEMOS_PASSWORD", "test-password")

ROOT = pathlib.Path(__file__).resolve().parents[2]
ZIP = ROOT / "testdata" / "takeout-test.zip"
EXPECTED_SKIPPED = 5  # fixture has 6 notes; the trashed one is skipped


def main() -> int:
    signin = json.dumps({"passwordCredentials": {"username": USER, "password": PASSWORD}}).encode()
    r = urllib.request.Request(BASE + "/api/v1/auth/signin", data=signin,
                               headers={"Content-Type": "application/json"}, method="POST")
    tok = json.load(urllib.request.urlopen(r))["accessToken"]
    r = urllib.request.Request(BASE + f"/api/v1/users/{USER}/personalAccessTokens",
                               data=json.dumps({"description": "idempotency-test"}).encode(),
                               headers={"Content-Type": "application/json",
                                        "Authorization": f"Bearer {tok}"},
                               method="POST")
    pat = json.load(urllib.request.urlopen(r))["token"]

    res = subprocess.run(
        [sys.executable, "-m", "keep_to_memos", "--url", BASE, "--token", pat, str(ZIP)],
        capture_output=True, text=True, cwd=ROOT,
    )
    print(res.stdout)
    if res.stderr:
        print("STDERR:", res.stderr)

    skipped = [ln for ln in res.stdout.splitlines() if "SKIP (already imported" in ln]
    ok = [ln for ln in res.stdout.splitlines() if "] OK:" in ln]
    failed = [ln for ln in res.stdout.splitlines() if "FAIL" in ln]
    print(f"\nsummary: {len(ok)} imported (expected 0), "
          f"{len(skipped)} skipped-already (expected {EXPECTED_SKIPPED}), "
          f"{len(failed)} failed (expected 0)")
    return 0 if (not ok and not failed and len(skipped) == EXPECTED_SKIPPED) else 1


if __name__ == "__main__":
    sys.exit(main())
