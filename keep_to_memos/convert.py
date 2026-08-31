"""Convert Keep notes to Memos memo content."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from keep_to_memos.keep import KeepNote

# Characters memos' tag parser accepts after the introducer.
_TAG_BAD = re.compile(r"[^0-9A-Za-z_-]")

_EMPTY_NOTE_PLACEHOLDER = "*(empty Google Keep note)*"

# Keep's default color is not a user-chosen label; skip it so we don't stamp
# every uncolored note with #color/default.
_SKIP_COLORS = {"", "default"}


def sanitize_tag(label: str) -> str:
    """Map a Keep label to a valid memos tag (spaces are not allowed)."""
    tag = _TAG_BAD.sub("-", label.strip().replace(" ", "-"))
    tag = re.sub(r"-{2,}", "-", tag).strip("-")
    return tag


def guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    if mime:
        return mime
    # Keep exports images with generic names; sniff magic bytes.
    try:
        with open(path, "rb") as f:
            magic = f.read(12)
    except OSError:
        return "application/octet-stream"
    if magic.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if magic.startswith(b"\x89PNG"):
        return "image/png"
    if magic[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if magic.startswith(b"RIFF") and magic[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def escape_md(text: str) -> str:
    """Escape lines that would become Markdown headings, leave prose alone."""
    lines = []
    for line in text.splitlines() or [""]:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            indent = line[: len(line) - len(stripped)]
            lines.append(indent + "\\" + stripped)
        else:
            lines.append(line)
    return "\n".join(lines)


def build_memo_body(note: KeepNote, attachment_names: list[str]) -> str:
    """Build the memo content: title heading, text, embedded attachments and
    #tags from labels. Returns a placeholder for notes that would otherwise be
    empty."""
    parts: list[str] = []
    if note.title:
        parts.append(f"# {escape_md(note.title)}")
    if note.text:
        parts.append(escape_md(note.text))
    for name in attachment_names:
        uid = name.split("/")[-1]
        parts.append(f"![](/file/attachments/{uid})")
    tags = []
    for label in note.labels:
        tag = sanitize_tag(label)
        if tag:
            tags.append(f"#{tag}")
    if tags:
        parts.append(" ".join(tags))
    color_tag = sanitize_tag(note.color.lower())
    if color_tag and color_tag.lower() not in _SKIP_COLORS:
        parts.append(f"#color/{color_tag.lower()}")
    return "\n\n".join(p for p in parts if p) or _EMPTY_NOTE_PLACEHOLDER
