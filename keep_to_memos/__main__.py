#!/usr/bin/env python3
"""Command-line entry point for the Keep -> Memos migration.

Usage:
  python -m keep_to_memos --url http://localhost:5230 --token TOKEN /path/to/Takeout
  python -m keep_to_memos --url ... --token TOKEN /path/to/takeout-*.zip

The token is a personal access token (memos_pat_...) created in the Memos UI
(Settings -> Access Tokens) or via
POST /api/v1/users/{user}/personalAccessTokens. It can also be passed as
MEMOS_TOKEN so it does not appear in the shell history.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from keep_to_memos import __version__
from keep_to_memos.client import MemosClient, MemosError
from keep_to_memos.keep import TakeoutError, load_takeout
from keep_to_memos.migrate import migrate


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="keep-to-memos",
        description="Migrate a Google Keep Takeout export to Memos.",
    )
    p.add_argument(
        "source", type=Path, help="Takeout Keep folder, Takeout root, or takeout zip"
    )
    p.add_argument(
        "--url",
        default=os.environ.get("MEMOS_URL", "http://localhost:5230"),
        help="Memos instance URL (default: %(default)s, or MEMOS_URL)",
    )
    p.add_argument(
        "--token",
        default=os.environ.get("MEMOS_TOKEN"),
        help="Memos personal access token (memos_pat_...; or set MEMOS_TOKEN)",
    )
    p.add_argument(
        "--dry-run", action="store_true", help="Parse and print without writing to memos"
    )
    p.add_argument("--include-trashed", action="store_true", help="Also import trashed notes")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)

    if not args.token:
        p.error("--token is required (or set MEMOS_TOKEN)")

    src = args.source.expanduser().resolve()
    if not src.exists():
        p.error(f"source path does not exist: {src}")

    client = MemosClient(args.url, args.token, dry_run=args.dry_run)

    try:
        me = client.whoami()
        print(f"Connected to {args.url} as {me.get('displayName') or me.get('username') or '?'}")
    except MemosError as e:
        if args.dry_run:
            print(f" (dry-run: skipping connection check: {e})")
        else:
            print(str(e), file=sys.stderr)
            return 1

    try:
        with load_takeout(src) as notes:
            if not notes:
                print("No notes found in export.")
                return 0
            return migrate(client, notes, skip_trashed=not args.include_trashed)
    except TakeoutError as e:
        print(str(e), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
