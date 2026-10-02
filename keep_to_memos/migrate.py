"""Migration driver: import parsed Keep notes into Memos."""

from __future__ import annotations

import hashlib
import sys

from keep_to_memos.client import MemosClient, MemosError, rfc3339
from keep_to_memos.convert import build_memo_body, guess_mime
from keep_to_memos.keep import KeepNote


def stable_memo_id(note: KeepNote) -> str:
    """Deterministic memo ID from the note's identity, so re-running the
    migration is idempotent: a second import of the same Takeout skips
    instead of duplicating. Memos requires IDs matching
    ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,34}[a-zA-Z0-9])?$ (36 chars max).
    """
    key = f"{note.title}|{note.created_ts}|{note.updated_ts}|{note.text[:80]}"
    return "keep-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]


def migrate(client: MemosClient, notes: list[KeepNote], skip_trashed: bool = True) -> int:
    """Import `notes` via `client`. Returns a process exit code (0 on
    success, 1 if any note failed)."""
    total = len(notes)
    imported = skipped = failed = 0

    print(f"Importing {total} note(s)...")
    for idx, note in enumerate(notes, 1):
        desc = note.title or (note.text.splitlines() or ["<empty>"])[0][:40]
        if note.trashed and skip_trashed:
            print(f"[{idx}/{total}] SKIP (trashed): {desc}")
            skipped += 1
            continue
        if client.dry_run:
            print(f"[{idx}/{total}] DRY-RUN: {desc}")
            imported += 1
            continue
        memo_id = stable_memo_id(note)
        try:
            # Idempotency: if this note was already imported AND is complete,
            # skip. A memo with empty content is the orphan of a previously
            # failed run — delete it and re-import.
            existing = client.get_memo(f"memos/{memo_id}")
            if existing.get("name"):
                if existing.get("content"):
                    print(f"[{idx}/{total}] SKIP (already imported as {existing['name']}): {desc}")
                    skipped += 1
                    continue
                print(f"[{idx}/{total}] NOTE: removing incomplete memo from a failed run: {desc}")
                client.delete_memo(existing["name"])
        except MemosError as e:
            if "not found" in str(e).lower():
                pass  # not imported yet — proceed to create
            else:
                # Can't tell whether it's already imported; do not create
                # (that could duplicate) — count it failed and move on.
                print(f"[{idx}/{total}] FAIL: {desc}: {e}", file=sys.stderr)
                failed += 1
                continue
        memo_name = None
        try:
            # 1. Create the memo first so attachments can be bound to it.
            state = "ARCHIVED" if note.archived else "NORMAL"
            memo = client.create_memo(
                content="",
                created_ts=note.created_ts,
                updated_ts=note.updated_ts,
                state=state,
                memo_id=memo_id,
            )
            memo_name = memo["name"]

            # 2. Upload attachments unbound, then attach them to the memo.
            #    Creating an attachment bound at creation fails with a
            #    server-side SQLITE_BUSY on some memos builds; the
            #    unbound-create + SetMemoAttachments flow works reliably.
            attachment_names = []
            for media in note.attachments:
                content = media.read_bytes()
                if not content:
                    continue
                att = client.create_attachment(
                    filename=media.name,
                    content=content,
                    mime_type=guess_mime(media),
                )
                attachment_names.append(att["name"])

            if attachment_names:
                client.set_memo_attachments(memo_name, attachment_names)

            # 3. Patch final content (referencing the attachments) and pin
            #    state. updateTime is re-asserted because SetMemoAttachments
            #    bumps it server-side.
            body = build_memo_body(note, attachment_names)
            fields = {
                "name": memo_name,
                "content": body,
                "state": state,
                "visibility": "PRIVATE",
                "updateTime": rfc3339(note.updated_ts),
            }
            mask = ["content", "state", "visibility", "update_time"]
            if note.pinned:
                fields["pinned"] = True
                mask.append("pinned")
            client.patch_memo(memo_name, fields, mask)

            print(f"[{idx}/{total}] OK: {desc} ({len(attachment_names)} attachment(s))")
            imported += 1
        except (MemosError, OSError, KeyError) as e:
            print(f"[{idx}/{total}] FAIL: {desc}: {e}", file=sys.stderr)
            # Best-effort cleanup: a half-imported memo would otherwise block
            # re-imports via the idempotency check.
            if memo_name:
                try:
                    client.delete_memo(memo_name)
                    print(
                        f"[{idx}/{total}] cleanup: deleted partial memo {memo_name}",
                        file=sys.stderr,
                    )
                except MemosError as ce:
                    print(f"[{idx}/{total}] cleanup failed for {memo_name}: {ce}", file=sys.stderr)
            failed += 1
    print(f"\nDone. imported={imported} skipped={skipped} failed={failed}")
    return 1 if failed else 0
