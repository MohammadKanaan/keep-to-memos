"""Tests for Keep Takeout parsing."""

import zipfile

import pytest

from keep_to_memos.keep import TakeoutError, _keep_usec_to_ts, load_takeout, parse_keep_note


def _notes(path):
    with load_takeout(path) as notes:
        return list(notes)


class TestTimestamps:
    @pytest.mark.parametrize("value,expected", [
        (1500000000000000, 1500000000),  # microseconds — what Keep exports
        (1500000000000, 1500000000),     # milliseconds
        (1500000000, 1500000000),        # seconds
        ("1500000000000000", 1500000000),
        (None, None),
        ("not-a-number", None),
    ])
    def test_cases(self, value, expected):
        assert _keep_usec_to_ts(value) == expected


class TestParseKeepNote:
    def test_full_note(self, tmp_path):
        (tmp_path / "Note.json").write_text(
            '{"title": "T", "textContent": "body", "labels": ["b", "a", "a"],'
            ' "color": "BLUE", "isPinned": true, "isArchived": false,'
            ' "isTrashed": false, "createdTimestampUsec": 1500000000000000,'
            ' "userEditedTimestampUsec": 1510000000000000}',
            encoding="utf-8",
        )
        note = parse_keep_note(tmp_path / "Note.json")
        assert note.title == "T"
        assert note.text == "body"
        assert note.labels == ["a", "b"]  # deduped + sorted
        assert note.color == "BLUE"
        assert note.pinned is True
        assert note.created_ts == 1500000000
        assert note.updated_ts == 1510000000

    def test_labels_as_keep_objects(self, tmp_path):
        # Real Takeout exports labels as [{"name": "..."}], not strings.
        (tmp_path / "Note.json").write_text(
            '{"title": "T", "labels": [{"name": "Work"}, {"name": "a"},'
            ' {"name": "a"}, {"name": ""}]}',
            encoding="utf-8",
        )
        assert parse_keep_note(tmp_path / "Note.json").labels == ["Work", "a"]

    def test_utf8_bom(self, tmp_path):
        (tmp_path / "Note.json").write_bytes(
            b'\xef\xbb\xbf{"title": "bom"}'
        )
        assert parse_keep_note(tmp_path / "Note.json").title == "bom"

    def test_defaults(self, tmp_path):
        (tmp_path / "Note.json").write_text("{}", encoding="utf-8")
        note = parse_keep_note(tmp_path / "Note.json")
        assert note.title == ""
        assert note.labels == []
        assert not note.pinned and not note.archived and not note.trashed
        assert note.created_ts == note.updated_ts  # falls back to now

    def test_non_object_json_returns_none(self, tmp_path, capsys):
        (tmp_path / "Arr.json").write_text("[]", encoding="utf-8")
        assert parse_keep_note(tmp_path / "Arr.json") is None
        assert "must be an object" in capsys.readouterr().err

    def test_unreadable_json_returns_none(self, tmp_path, capsys):
        (tmp_path / "Bad.json").write_text("{not json", encoding="utf-8")
        assert parse_keep_note(tmp_path / "Bad.json") is None
        assert "skipping unreadable" in capsys.readouterr().err

    def test_attachments_matched_by_stem(self, tmp_path):
        # Media sits next to the JSON named after the note.
        (tmp_path / "Note.json").write_text("{}", encoding="utf-8")
        (tmp_path / "Note.png").write_bytes(b"\x89PNG")
        (tmp_path / "Note extra.jpg").write_bytes(b"\xff\xd8")
        (tmp_path / "Other.png").write_bytes(b"\x89PNG")
        (tmp_path / "meta.json").write_text("{}", encoding="utf-8")
        note = parse_keep_note(tmp_path / "Note.json")
        assert [a.name for a in note.attachments] == ["Note extra.jpg", "Note.png"]

    def test_html_preview_not_an_attachment(self, tmp_path):
        # Google ships a .html preview next to every note JSON; it must not
        # be picked up as an attachment by the stem fallback.
        (tmp_path / "Note.json").write_text("{}", encoding="utf-8")
        (tmp_path / "Note.html").write_text("<html></html>", encoding="utf-8")
        note = parse_keep_note(tmp_path / "Note.json")
        assert note.attachments == []

    def test_missing_attachment_warned(self, tmp_path, capsys):
        (tmp_path / "Note.json").write_text(
            '{"attachments": [{"filePath": "gone.png"}]}',
            encoding="utf-8",
        )
        note = parse_keep_note(tmp_path / "Note.json")
        assert note.attachments == []
        assert "missing attachment gone.png" in capsys.readouterr().err

    def test_attachment_outside_export_skipped(self, tmp_path, capsys):
        (tmp_path / "Note.json").write_text(
            '{"attachments": [{"filePath": "../secret.png"}]}',
            encoding="utf-8",
        )
        (tmp_path.parent / "secret.png").write_bytes(b"\x89PNG")
        note = parse_keep_note(tmp_path / "Note.json")
        assert note.attachments == []
        assert "outside export" in capsys.readouterr().err

    def test_list_note(self, tmp_path):
        # Checklist notes carry items in `listContent` with per-item
        # `isChecked`; render them as markdown task lines.
        (tmp_path / "List.json").write_text(
            '{"title": "liste", "listContent": ['
            ' {"text": "yep", "isChecked": false, "textHtml": "<p>yep</p>"},'
            ' {"text": "no", "isChecked": true},'
            ' {"text": "mhmmm", "isChecked": false}]}',
            encoding="utf-8",
        )
        note = parse_keep_note(tmp_path / "List.json")
        assert note.text == "- [ ] yep\n- [x] no\n- [ ] mhmmm"

    def test_list_wins_over_text_content(self, tmp_path):
        # If a JSON somehow has both, the checklist is the real content.
        (tmp_path / "Both.json").write_text(
            '{"listContent": [{"text": "a", "isChecked": false}],'
            ' "textContent": "ignored"}',
            encoding="utf-8",
        )
        assert parse_keep_note(tmp_path / "Both.json").text == "- [ ] a"

    def test_list_content_skips_non_objects(self, tmp_path):
        (tmp_path / "List.json").write_text(
            '{"listContent": ["nope", {"text": "ok", "isChecked": true}]}',
            encoding="utf-8",
        )
        assert parse_keep_note(tmp_path / "List.json").text == "- [x] ok"


class TestLoadTakeout:
    def test_directory(self, takeout_dir):
        notes = _notes(takeout_dir)
        titles = {n.title for n in notes}
        assert titles == {"Shopping list", "Photo note", "Plain thought",
                          "Old recipe", "", "Deleted note"}

    def test_takeout_root_contains_keep(self, takeout_dir):
        # Passing the Takeout root (parent of Keep/) works too.
        assert len(_notes(takeout_dir.parent)) == 6

    def test_zip_archive(self, takeout_zip, tmp_path):
        import shutil
        copy = tmp_path / "takeout-test.zip"
        shutil.copy(takeout_zip, copy)
        assert len(_notes(copy)) == 6

    def test_zip_cleans_tempdir(self, takeout_zip, tmp_path):
        import shutil
        copy = tmp_path / "takeout-test.zip"
        shutil.copy(takeout_zip, copy)
        with load_takeout(copy) as notes:
            assert len(notes) == 6
        assert not (tmp_path / "_keep_extract").exists()

    def test_not_a_takeout(self, tmp_path):
        with pytest.raises(TakeoutError, match="does not look like"):
            with load_takeout(tmp_path):
                pass

    def test_plain_file_is_not_takeout(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("nope", encoding="utf-8")
        with pytest.raises(TakeoutError, match="not a Takeout zip"):
            with load_takeout(f):
                pass

    def test_bad_zip(self, tmp_path):
        z = tmp_path / "fake.zip"
        z.write_bytes(b"not a zip")
        with pytest.raises(TakeoutError, match="not a valid zip"):
            with load_takeout(z):
                pass

    def test_zip_slip_rejected(self, tmp_path):
        zf = tmp_path / "evil.zip"
        with zipfile.ZipFile(zf, "w") as z:
            z.writestr("Takeout/Keep/ok.json", '{"title": "ok"}')
            z.writestr("../evil.txt", "nope")
        with pytest.raises(TakeoutError, match="unsafe path"):
            with load_takeout(zf):
                pass

    def test_modern_layout_directory(self, tmp_path):
        # Modern exports put note JSONs (and media) directly in Keep/, and
        # reference attachments via attachments[].filePath.
        keep = tmp_path / "Keep"
        keep.mkdir()
        (keep / "Modern note.json").write_text(
            '{"title": "Modern", "textContent": "hi",'
            ' "attachments": [{"filePath": "photo.png", "mimetype": "image/png"}]}',
            encoding="utf-8",
        )
        (keep / "photo.png").write_bytes(b"\x89PNG")
        notes = _notes(keep)
        assert [n.title for n in notes] == ["Modern"]
        assert [a.name for a in notes[0].attachments] == ["photo.png"]

    def test_modern_layout_takeout_root(self, tmp_path):
        keep = tmp_path / "Takeout" / "Keep"
        keep.mkdir(parents=True)
        (keep / "Root note.json").write_text('{"title": "From root"}', encoding="utf-8")
        assert [n.title for n in _notes(tmp_path / "Takeout")] == ["From root"]

    def test_modern_layout_zip(self, tmp_path):
        zf = tmp_path / "modern.zip"
        with zipfile.ZipFile(zf, "w") as z:
            z.writestr("Takeout/Keep/Modern.json", '{"title": "Zipped"}')
            z.writestr("Takeout/Keep/photo.png", b"\x89PNG")
        assert [n.title for n in _notes(zf)] == ["Zipped"]

    def test_legacy_wins_over_modern(self, tmp_path):
        # A Keep dir with both layouts parses the legacy Notes/ only.
        keep = tmp_path / "Keep"
        (keep / "Notes").mkdir(parents=True)
        (keep / "Notes" / "Legacy.json").write_text('{"title": "Old"}', encoding="utf-8")
        (keep / "Modern.json").write_text('{"title": "New"}', encoding="utf-8")
        assert [n.title for n in _notes(keep)] == ["Old"]

    def test_modern_attachment_list_wins_over_stem(self, tmp_path):
        # When the JSON lists attachments, don't also pick up same-stem media.
        (tmp_path / "Note.json").write_text(
            '{"attachments": [{"filePath": "photo.png"}]}', encoding="utf-8"
        )
        (tmp_path / "photo.png").write_bytes(b"\x89PNG")
        (tmp_path / "Note.png").write_bytes(b"\x89PNG")
        note = parse_keep_note(tmp_path / "Note.json")
        assert [a.name for a in note.attachments] == ["photo.png"]

    def test_empty_attachment_list_skips_stem(self, tmp_path):
        (tmp_path / "Note.json").write_text('{"attachments": []}', encoding="utf-8")
        (tmp_path / "Note.png").write_bytes(b"\x89PNG")
        assert parse_keep_note(tmp_path / "Note.json").attachments == []
