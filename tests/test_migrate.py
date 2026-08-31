"""Tests for the migration driver, using a fake in-memory MemosClient.

Covers the import flow, idempotent re-runs, orphan cleanup, failure rollback,
and dry-run.
"""

import pytest

from keep_to_memos.client import MemosError
from keep_to_memos.keep import KeepNote, load_takeout
from keep_to_memos.migrate import migrate, stable_memo_id


class FakeMemosClient:
    """In-memory stand-in for MemosClient.

    Mimics the server behaviors migrate() depends on:
    - get_memo raises MemosError("...not found...") for unknown IDs
    - memos are stored keyed by memo ID with empty content until patched
    - set_memo_attachments bumps updateTime server-side
    """

    def __init__(self, notes: list[KeepNote] | None = None):
        self.dry_run = False
        self.memos: dict[str, dict] = {}  # memo_id -> memo dict
        self.attachment_bytes: dict[str, bytes] = {}
        self._next_att = 0
        if notes:
            for n in notes:
                self.memos[stable_memo_id(n)] = self._full_memo(n)

    def _full_memo(self, note: KeepNote) -> dict:
        return {
            "name": f"memos/{stable_memo_id(note)}",
            "content": "pre-existing",
            "createTime": note.created_ts,
            "updateTime": note.updated_ts,
            "pinned": note.pinned,
            "state": "ARCHIVED" if note.archived else "NORMAL",
            "attachments": [],
        }

    # --- MemosClient API -------------------------------------------------

    def get_memo(self, memo_name: str) -> dict:
        memo_id = memo_name.split("/")[-1]
        if memo_id not in self.memos:
            raise MemosError(f"memo not found: {memo_name}")
        return self.memos[memo_id]

    def create_memo(self, content, created_ts=None, updated_ts=None,
                    state="NORMAL", memo_id=None) -> dict:
        self.memos[memo_id] = {
            "name": f"memos/{memo_id}",
            "content": content,  # empty at creation
            "state": state,
        }
        return self.memos[memo_id]

    def create_attachment(self, filename, content, mime_type) -> dict:
        self._next_att += 1
        name = f"attachments/att{self._next_att}"
        self.attachment_bytes[name] = content
        return {"name": name, "filename": filename, "type": mime_type}

    def set_memo_attachments(self, memo_name, attachment_names) -> dict:
        memo = self.memos[memo_name.split("/")[-1]]
        memo["attachments"] = [{"name": n} for n in attachment_names]
        memo["updateTime"] = "bumped-server-side"
        return memo

    def patch_memo(self, memo_name, fields, update_mask) -> dict:
        memo = self.memos[memo_name.split("/")[-1]]
        for key in update_mask:
            if key == "update_time":
                key = "updateTime"
            if key in fields:
                memo[key] = fields[key]
        return memo

    def delete_memo(self, memo_name) -> dict:
        self.memos.pop(memo_name.split("/")[-1], None)
        return {}


def _note(**kwargs) -> KeepNote:
    fields = dict(title="T", text="body", created_ts=1500000000, updated_ts=1500000000)
    fields.update(kwargs)
    return KeepNote(**fields)


@pytest.fixture
def takeout_notes(takeout_dir):
    with load_takeout(takeout_dir) as notes:
        yield notes


class TestStableMemoId:
    def test_deterministic(self):
        assert stable_memo_id(_note()) == stable_memo_id(_note())

    def test_different_notes_differ(self):
        assert stable_memo_id(_note(title="A")) != stable_memo_id(_note(title="B"))

    def test_respects_memos_id_charset_and_length(self):
        # memos requires ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,34}[a-zA-Z0-9])?$
        import re
        mid = stable_memo_id(_note(title="w" * 500, text="x" * 500))
        assert re.fullmatch(r"[a-zA-Z0-9]([a-zA-Z0-9-]{0,34}[a-zA-Z0-9])?", mid)


class TestMigrate:
    def test_full_import(self, takeout_notes, capsys):
        client = FakeMemosClient()
        rc = migrate(client, takeout_notes)
        out = capsys.readouterr().out
        assert rc == 0
        assert len(client.memos) == 5  # 6 notes, 1 trashed skipped
        assert "SKIP (trashed): Deleted note" in out

    def test_trashed_included_with_flag(self, takeout_notes):
        client = FakeMemosClient()
        migrate(client, takeout_notes, skip_trashed=False)
        assert len(client.memos) == 6

    def test_imported_memo_content(self, takeout_notes):
        # The Shopping list fixture has labels, color, and pinned set.
        client = FakeMemosClient()
        migrate(client, takeout_notes)
        shopping = [m for m in client.memos.values() if "Shopping" in m.get("content", "")]
        assert len(shopping) == 1
        memo = shopping[0]
        assert memo["content"].startswith("# Shopping list")
        assert "#errands #groceries" in memo["content"]
        assert "#color/blue" in memo["content"]
        assert memo["pinned"] is True

    def test_archived_note_state(self, takeout_notes):
        client = FakeMemosClient()
        migrate(client, takeout_notes)
        recipe = [m for m in client.memos.values() if "flour" in m.get("content", "")]
        assert recipe[0]["state"] == "ARCHIVED"

    def test_attachment_upload_and_embed(self, takeout_notes):
        client = FakeMemosClient()
        migrate(client, takeout_notes)
        photo = [m for m in client.memos.values() if "Whiteboard" in m.get("content", "")]
        names = [a["name"] for a in photo[0]["attachments"]]
        assert len(names) == 1
        assert f"![](/file/attachments/{names[0].split('/')[-1]})" in photo[0]["content"]
        # bytes preserved through the base64 round trip
        assert client.attachment_bytes[names[0]].startswith(b"\x89PNG")

    def test_update_time_reasserted_after_attachment_bump(self, takeout_notes):
        client = FakeMemosClient()
        migrate(client, takeout_notes)
        photo = [m for m in client.memos.values() if "Whiteboard" in m.get("content", "")][0]
        # SetMemoAttachments bumps updateTime server-side; the final PATCH
        # must re-assert the original Keep date.
        assert photo["updateTime"] == "2017-07-14T02:40:50Z"

    def test_exit_code_1_on_failure(self, takeout_notes):
        client = FakeMemosClient()

        def failing_create(*a, **kw):
            raise MemosError("HTTP 500: down")

        client.create_memo = failing_create
        assert migrate(client, takeout_notes) == 1


class TestIdempotency:
    def test_second_run_is_noop(self, takeout_notes, capsys):
        client = FakeMemosClient()
        migrate(client, takeout_notes)
        capsys.readouterr()
        rc = migrate(client, takeout_notes)
        out = capsys.readouterr().out
        assert rc == 0
        assert not [ln for ln in out.splitlines() if "] OK:" in ln]
        assert len([ln for ln in out.splitlines() if "SKIP (already imported" in ln]) == 5

    def test_orphan_from_failed_run_reimported(self, takeout_notes, capsys):
        # A memo with empty content is the orphan of a previously failed run.
        client = FakeMemosClient()
        orphan_id = stable_memo_id(takeout_notes[2])
        client.memos[orphan_id] = {"name": f"memos/{orphan_id}", "content": ""}
        rc = migrate(client, takeout_notes)
        out = capsys.readouterr().out
        assert rc == 0
        assert "removing incomplete memo" in out
        assert client.memos[orphan_id]["content"]  # re-imported with content


class TestCleanupOnFailure:
    def test_partial_memo_deleted_on_attachment_failure(self, takeout_notes):
        client = FakeMemosClient()

        def failing_set(memo_name, names):
            raise MemosError("HTTP 500: busy")

        client.set_memo_attachments = failing_set
        rc = migrate(client, takeout_notes)
        assert rc == 1
        # The half-imported photo memo was cleaned up, others survived.
        assert not [i for i, m in client.memos.items() if not m.get("content")]
        assert any("flour" in m.get("content", "") for m in client.memos.values())


class TestDryRun:
    def test_writes_nothing(self, takeout_notes, capsys):
        client = FakeMemosClient()
        client.dry_run = True
        rc = migrate(client, takeout_notes)
        out = capsys.readouterr().out
        assert rc == 0
        assert client.memos == {} and client.attachment_bytes == {}
        assert out.count("DRY-RUN") == 5
