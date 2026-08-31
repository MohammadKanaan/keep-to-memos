"""Tests for Keep -> memo content conversion."""

import pytest

from keep_to_memos.convert import build_memo_body, escape_md, guess_mime, sanitize_tag
from keep_to_memos.keep import KeepNote


class TestSanitizeTag:
    @pytest.mark.parametrize("label,expected", [
        ("errands", "errands"),
        ("shopping list", "shopping-list"),
        ("Work Stuff!!", "Work-Stuff"),
        ("  spaced  ", "spaced"),
        ("---", ""),
    ])
    def test_cases(self, label, expected):
        assert sanitize_tag(label) == expected


class TestEscapeMd:
    def test_heading_lines_escaped(self):
        assert escape_md("# heading\nnormal text") == "\\# heading\nnormal text"

    def test_indented_heading_escaped(self):
        # Indentation preserved, backslash inserted before the hashes.
        assert escape_md("   ## deep") == "   \\## deep"

    def test_prose_untouched(self):
        text = "plain # not a heading midline\nother"
        assert escape_md(text) == text


class TestBuildMemoBody:
    def test_full_note(self):
        note = KeepNote(title="T", text="body", labels=["a", "b"], color="BLUE")
        body = build_memo_body(note, ["attachments/uid1"])
        assert body == (
            "# T\n\nbody\n\n![](/file/attachments/uid1)\n\n#a #b\n\n#color/blue"
        )

    def test_empty_note_gets_placeholder(self):
        assert build_memo_body(KeepNote(), []) == "*(empty Google Keep note)*"

    def test_default_color_not_tagged(self):
        body = build_memo_body(KeepNote(text="x", color="DEFAULT"), [])
        assert "#color" not in body

    def test_empty_sanitized_label_omitted(self):
        body = build_memo_body(KeepNote(text="x", labels=["---"]), [])
        assert body == "x"

    def test_attachment_uid_extraction(self):
        # Attachment names are like "attachments/<uid>"; only the uid is used.
        body = build_memo_body(KeepNote(text="x"), ["attachments/abc123"])
        assert "![](/file/attachments/abc123)" in body


class TestGuessMime:
    @pytest.mark.parametrize("magic,expected", [
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"GIF89a\x00\x00", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBP", "image/webp"),
        (b"\x00\x01\x02", "application/octet-stream"),
    ])
    def test_magic_sniff(self, tmp_path, magic, expected):
        # Extensionless name forces the magic-byte fallback: Keep exports
        # images under generic names.
        f = tmp_path / "note"
        f.write_bytes(magic + b"0" * 20)
        assert guess_mime(f) == expected

    def test_extension(self, tmp_path):
        f = tmp_path / "note.png"
        f.write_bytes(b"not a real png")
        assert guess_mime(f) == "image/png"

    def test_missing_file(self, tmp_path):
        assert guess_mime(tmp_path / "gone") == "application/octet-stream"
