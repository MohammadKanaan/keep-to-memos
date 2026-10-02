#!/usr/bin/env python3
"""Command-line entry point for the Keep -> Memos migration.

Usage:
  python -m keep_to_memos --url http://localhost:5230 --token TOKEN /path/to/Takeout
  python -m keep_to_memos --url ... --token TOKEN /path/to/takeout-*.zip

The token is a personal access token (memos_pat_...) created in the Memos UI
(Settings -> Access Tokens) or via
POST /api/v1/users/{user}/personalAccessTokens. It can also be passed as
MEMOS_TOKEN so it does not appear in the shell history.

When run from a terminal and the source, URL, or token is missing, they are
prompted for interactively (the token hidden); scripts and CI keep failing
fast.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from keep_to_memos import __version__
from keep_to_memos.client import DEFAULT_WRITE_DELAY, MemosClient, MemosError
from keep_to_memos.keep import TakeoutError, load_takeout
from keep_to_memos.migrate import migrate

DEFAULT_URL = "http://localhost:5230"


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _prompt_source() -> str:
    return input("Path to the Takeout export (zip or Keep folder): ").strip()


def _prompt_url(default: str) -> str:
    entered = input(f"Memos URL [{default}]: ").strip()
    return entered or default


def _prompt_token() -> str:
    print("A personal access token is required.")
    print("Create one in Memos under Settings -> Access Tokens.")
    return getpass.getpass("Token (memos_pat_...): ").strip()


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="keep-to-memos",
        description="Migrate a Google Keep Takeout export to Memos.",
    )
    p.add_argument(
        "source",
        type=Path,
        nargs="?",
        help="Takeout Keep folder, Takeout root, or takeout zip (prompted if omitted)",
    )
    p.add_argument(
        "--url",
        default=None,
        help=f"Memos instance URL (default: {DEFAULT_URL}, or MEMOS_URL)",
    )
    p.add_argument(
        "--token",
        default=None,
        help="Memos personal access token (memos_pat_...; or set MEMOS_TOKEN)",
    )
    p.add_argument(
        "--dry-run", action="store_true", help="Parse and print without writing to memos"
    )
    p.add_argument("--include-trashed", action="store_true", help="Also import trashed notes")
    p.add_argument(
        "--no-throttle",
        action="store_true",
        help="Skip the 0.5s pause between writes (safe on non-SQLite backends "
        "like Postgres/MySQL)",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)

    try:
        if args.source is None:
            if not _is_interactive():
                p.error("the following arguments are required: source")
            entered = _prompt_source()
            if not entered:
                p.error("a source path is required")
            args.source = Path(entered)

        src = args.source.expanduser().resolve()
        if not src.exists():
            p.error(f"source path does not exist: {src}")

        url = args.url or os.environ.get("MEMOS_URL") or DEFAULT_URL
        token = args.token or os.environ.get("MEMOS_TOKEN")

        if not args.dry_run and _is_interactive():
            if args.url is None and "MEMOS_URL" not in os.environ:
                url = _prompt_url(DEFAULT_URL)
            if not token:
                token = _prompt_token()
    except EOFError:
        # Ctrl-D at a prompt: abort without a traceback.
        print("Input closed before prompts finished; aborting.", file=sys.stderr)
        return 1

    if not args.dry_run and not token:
        p.error("--token is required (or set MEMOS_TOKEN)")

    client = MemosClient(
        url,
        token or "",
        dry_run=args.dry_run,
        write_delay=0.0 if args.no_throttle else DEFAULT_WRITE_DELAY,
    )

    if args.dry_run and not token:
        # Nothing to import and no credentials: stay fully offline.
        print("(dry-run: no token, skipping connection check)")
    else:
        try:
            me = client.whoami()
            print(
                f"Connected to {url} as "
                f"{me.get('displayName') or me.get('username') or '?'}"
            )
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
    except MemosError as e:
        # migrate() treats per-note failures as non-fatal, but any MemosError
        # that escapes it (or the connection check) should be a clean error,
        # not a traceback.
        print(str(e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
