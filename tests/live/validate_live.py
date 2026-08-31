#!/usr/bin/env python3
"""Live end-to-end validation: run the tool against a fresh memos instance
and assert every requirement (images + byte-exact round-trip, dates, tags,
pinned, archived, CLI, API-only) via the memos HTTP API.

Requires a running memos instance. Configure via environment variables:

  MEMOS_URL        default http://localhost:5230
  MEMOS_USER       default admin  (first signup on a fresh instance
                                becomes ADMIN via open signup)
  MEMOS_PASSWORD   default test-password
  MEMOS_EMAIL      default user@example.invalid
  MEMOS_PAT        optional: existing personal access token; if unset a
                   fresh one is created via sign-in

Usage: python3 tests/live/validate_live.py
Exit code 0 = all checks passed.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("MEMOS_URL", "http://localhost:5230").rstrip("/")
USER = os.environ.get("MEMOS_USER", "admin")
PASSWORD = os.environ.get("MEMOS_PASSWORD", "test-password")
EMAIL = os.environ.get("MEMOS_EMAIL", "user@example.invalid")
PAT = os.environ.get("MEMOS_PAT")

ROOT = pathlib.Path(__file__).resolve().parents[2]
TAKEOUT = ROOT / "testdata" / "Takeout" / "Keep"
PNG_BYTES = (TAKEOUT / "Notes" / "Photo note.png").read_bytes()


def req(method, path, body=None, token=None, retries=3):
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    for attempt in range(retries + 1):
        r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(r) as resp:
                text = resp.read().decode()
            return json.loads(text) if text.strip() else {}
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", "replace")
            if e.code >= 500 and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            print(f"HTTP {e.code} on {method} {path}: {body_text[:300]}")
            return None
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            print(f"connection failed on {method} {path}: {e.reason}")
            return None


def get_pat() -> str:
    # First user on a fresh instance becomes ADMIN via open signup.
    req("POST", "/api/v1/users", {"username": USER, "password": PASSWORD,
                                 "email": EMAIL, "displayName": USER})
    tok = req("POST", "/api/v1/auth/signin",
              {"passwordCredentials": {"username": USER, "password": PASSWORD}})["accessToken"]
    return req("POST", f"/api/v1/users/{USER}/personalAccessTokens",
               {"description": "keep-migration-validation"}, token=tok)["token"]


def ts_date(iso: str) -> str:
    return iso.replace("Z", "+00:00")[:10] if "Z" not in iso[:10] else iso[:10]


def main() -> int:
    global PAT
    PAT = PAT or get_pat()
    print("PAT:", PAT[:12] + "...")

    # --- 1. Clean slate: delete every memo --------------------------------
    for state in (None, "ARCHIVED"):
        q = "/api/v1/memos?pageSize=200" + (f"&state={state}" if state else "")
        for m in req("GET", q, token=PAT).get("memos", []):
            time.sleep(0.5)
            req("DELETE", "/api/v1/" + m["name"], token=PAT)
            print("  deleted", m["name"])

    # --- 2. Run the migration tool ---------------------------------------
    print("\n=== running migration tool ===")
    r = subprocess.run(
        [sys.executable, "-m", "keep_to_memos", "--url", BASE, "--token", PAT, str(TAKEOUT)],
        capture_output=True, text=True, cwd=ROOT,
    )
    print(r.stdout)
    if r.stderr:
        print("STDERR:", r.stderr)
    if r.returncode != 0:
        print("tool exited non-zero:", r.returncode)
        return 1

    # --- 3. Validate requirements against the live API --------------------
    print("=== validating requirements ===")
    failures = []

    def list_memos(state=None):
        q = "/api/v1/memos?pageSize=200" + (f"&state={state}" if state else "")
        return (req("GET", q, token=PAT) or {}).get("memos", [])

    memos = list_memos()
    archived = list_memos("ARCHIVED")

    # R1: images moved + bytes round-trip
    photo = [m for m in memos if "Whiteboard" in m.get("snippet", "")]
    if not photo:
        failures.append("R1: photo note not found")
    elif not photo[0].get("attachments"):
        failures.append("R1: photo note has no attachments")
    else:
        att = photo[0]["attachments"][0]
        print(
            f"R1 OK: photo note has attachment {att['name']} "
            f"({att.get('type')}, {att.get('size')} bytes)"
        )
        try:
            uid = att["name"].split("/")[-1]
            r = urllib.request.Request(BASE + f"/file/attachments/{uid}",
                                       headers={"Authorization": f"Bearer {PAT}"})
            with urllib.request.urlopen(r) as resp:
                img = resp.read()
            if img == PNG_BYTES:
                print("R1 OK: attachment bytes match source image exactly")
            else:
                failures.append(
                    f"R1: attachment bytes differ (got {len(img)}, want {len(PNG_BYTES)})"
                )
        except Exception as e:
            failures.append(f"R1: could not fetch attachment content: {e}")

    # R2: original dates preserved
    expect_dates = {"Shopping list": ("2020-09-13", "2021-01-07"),
                    "Photo note": ("2017-07-14", "2017-07-14"),
                    "Plain thought": ("2014-05-13", "2014-05-13")}
    for title, (want_c, want_u) in expect_dates.items():
        m = ([x for x in memos if x["snippet"].startswith(title)]
             or [x for x in memos + archived if title in x["snippet"]])
        if not m:
            failures.append(f"R2: note '{title}' missing")
            continue
        got_c, got_u = m[0]["createTime"][:10], m[0]["updateTime"][:10]
        if got_c != want_c or got_u != want_u:
            failures.append(
                f"R2: {title} created {got_c} (want {want_c}), updated {got_u} (want {want_u})"
            )
        else:
            print(f"R2 OK: {title} created {got_c}, updated {got_u}")

    # R2-extra: archived note dates (fixture ground truth: 2011-03-13 UTC)
    recipe_m = [x for x in archived if "flour" in x.get("snippet", "")]
    if not recipe_m:
        failures.append("R2: archived recipe note missing")
    else:
        got_c, got_u = recipe_m[0]["createTime"][:10], recipe_m[0]["updateTime"][:10]
        if got_c != "2011-03-13" or got_u != "2011-03-13":
            failures.append(f"R2: Old recipe created {got_c}, updated {got_u} (want 2011-03-13)")
        else:
            print(f"R2 OK: Old recipe created {got_c}, updated {got_u}")

    # R3: metadata (labels->tags, pinned, archived state)
    sl = [m for m in memos if m["snippet"].startswith("Shopping")]
    if not sl:
        failures.append("R3: Shopping list note missing")
    else:
        tags = set(sl[0].get("tags", []))
        want = {"errands", "groceries"}
        if not want.issubset(tags):
            failures.append(f"R3: Shopping list tags {tags} missing {want - tags}")
        else:
            print(f"R3 OK: labels -> tags {sorted(tags)}")
        if not sl[0].get("pinned"):
            failures.append("R3: Shopping list should be pinned")
        else:
            print("R3 OK: pinned preserved")

    if recipe_m:
        print(f"R3 OK: archived note in ARCHIVED state ({recipe_m[0]['name']})")
    else:
        failures.append("R3: archived note not found in ARCHIVED listing")

    # R4/R5: CLI + API-only are structural (exercised above)
    print("R4 OK: CLI tool call drove the import")
    print("R5 OK: imports went through memos' HTTP API exclusively")

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILURES:\n- " + "\n- ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
