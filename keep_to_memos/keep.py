"""Google Keep Takeout export parsing.

Two layouts are supported:

- Legacy: a `Keep/Notes/` directory where each `<note>.json` file holds the
  note's content and metadata, and media files sit next to the JSON named
  after the note (`<note title>.png`, etc.).
- Modern: note JSONs (and their media) sit directly in the `Keep/` directory,
  and attachments are listed in each JSON under `attachments[].filePath`.

Legacy layout is preferred when both are present.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class TakeoutError(Exception):
    """The path is not a usable Google Keep Takeout export."""


@dataclass
class KeepNote:
    title: str = ""
    text: str = ""
    labels: list[str] = field(default_factory=list)
    color: str = ""
    pinned: bool = False
    archived: bool = False
    trashed: bool = False
    created_ts: int = 0
    updated_ts: int = 0
    attachments: list[Path] = field(default_factory=list)


def _keep_usec_to_ts(value: Any) -> int | None:
    """Keep exports timestamps as epoch microseconds; tolerate ms and s too."""
    if not value:
        return None
    try:
        usec = int(value)
    except (TypeError, ValueError):
        return None
    if usec > 10_000_000_000_000:  # microseconds
        return usec // 1_000_000
    if usec > 10_000_000_000:  # milliseconds
        return usec // 1_000
    return usec


def _parse_keep_timestamps(raw: dict[str, Any]) -> tuple[int, int]:
    created = (
        _keep_usec_to_ts(raw.get("createdTimestampUsec"))
        or _keep_usec_to_ts(raw.get("userEditedTimestampUsec"))
        or int(time.time())
    )
    updated = _keep_usec_to_ts(raw.get("userEditedTimestampUsec")) or created
    return created, updated


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _parse_labels(raw_labels: Any) -> list[str]:
    """Keep exports `labels` as `[{"name": "..."}]`; also accept plain strings."""
    names: set[str] = set()
    if not isinstance(raw_labels, list):
        return []
    for item in raw_labels:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = _as_str(item.get("name")).strip()
        else:
            continue
        if name:
            names.add(name)
    return sorted(names)


def _parse_list_content(list_items: Any) -> str | None:
    if not isinstance(list_items, list) or not list_items:
        return None
    lines: list[str] = []
    for item in list_items:
        if not isinstance(item, dict):
            continue
        checked = "x" if item.get("isChecked") else " "
        lines.append(f"- [{checked}] {_as_str(item.get('text'))}")
    return "\n".join(lines) if lines else None


def _is_inside(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _collect_attachments(json_path: Path, raw: dict[str, Any]) -> list[Path]:
    """Modern exports list attachments in the JSON (`attachments[].filePath`,
    relative to the Keep dir). Legacy exports name media after the note and
    place it next to the JSON; match by stem prefix, but a media file belongs
    to the note with the longest matching stem, so `Note trip.png` is not
    also claimed by `Note.json`. Prefer the JSON list.
    `.html` files are the per-note preview export Google ships next to every
    JSON, never Keep attachments, so the stem fallback skips them.
    """
    keep_dir = json_path.parent
    attachments: list[Path] = []
    listed = isinstance(raw.get("attachments"), list)
    if listed:
        for att in raw["attachments"]:
            if not isinstance(att, dict):
                continue
            fp = att.get("filePath")
            if not fp or not isinstance(fp, str):
                continue
            candidate = keep_dir / fp
            if not candidate.is_file():
                print(
                    f"  ! missing attachment {fp} for {json_path.name}",
                    file=sys.stderr,
                )
                continue
            if not _is_inside(keep_dir, candidate):
                print(
                    f"  ! skipped attachment outside export ({fp}) for {json_path.name}",
                    file=sys.stderr,
                )
                continue
            attachments.append(candidate)
    else:
        stems: set[str] = set()
        media: list[Path] = []
        for f in keep_dir.iterdir():
            if f.suffix.lower() == ".json":
                stems.add(f.stem)
            elif f.suffix.lower() != ".html":
                media.append(f)
        for f in media:
            owner = max((s for s in stems if f.stem.startswith(s)), key=len, default=None)
            if owner == json_path.stem:
                attachments.append(f)
    attachments.sort()
    return attachments


def parse_keep_note(json_path: Path) -> KeepNote | None:
    """Parse one Keep note JSON. Returns None (with a stderr warning) if the
    file is unreadable, not valid JSON, or not a note object."""
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ! skipping unreadable {json_path.name}: {e}", file=sys.stderr)
        return None
    if not isinstance(raw, dict):
        print(f"  ! skipping {json_path.name}: note JSON must be an object", file=sys.stderr)
        return None

    title = _as_str(raw.get("title")).strip()
    # A note is either plain text or a checklist. Real exports put list items
    # in `listContent` (with per-item `isChecked`); render them as markdown
    # task lines so memos shows them as a checkbox list.
    text = _parse_list_content(raw.get("listContent"))
    if text is None:
        text = _as_str(raw.get("textContent"))

    created, updated = _parse_keep_timestamps(raw)
    return KeepNote(
        title=title,
        text=text,
        labels=_parse_labels(raw.get("labels")),
        color=_as_str(raw.get("color")).strip(),
        pinned=bool(raw.get("isPinned")),
        archived=bool(raw.get("isArchived")),
        trashed=bool(raw.get("isTrashed")),
        created_ts=created,
        updated_ts=updated,
        attachments=_collect_attachments(json_path, raw),
    )


@contextmanager
def load_takeout(path: Path) -> Iterator[list[KeepNote]]:
    """Load all Keep notes from a Takeout directory or zip archive.

    `path` may be the `Keep` folder itself, a Takeout root containing `Keep/`,
    or a `takeout-*.zip`. Both the legacy (`Keep/Notes/`) and modern
    (JSONs in `Keep/` directly) layouts are accepted; legacy wins when both
    are present. Zip archives are extracted to a temporary directory that is
    deleted when the context exits, so use `with load_takeout(...) as notes`.
    """
    tmp: tempfile.TemporaryDirectory[str] | None = None
    try:
        keep_dir = _resolve_keep_dir(path)
        if keep_dir.is_file():
            if keep_dir.suffix.lower() != ".zip":
                raise TakeoutError(f"{path} is not a Takeout zip or Keep folder")
            tmp = tempfile.TemporaryDirectory(prefix="keep-to-memos-")
            keep_dir = _extract_zip(keep_dir, Path(tmp.name))

        notes_dir = _notes_dir(keep_dir)
        if notes_dir is None:
            raise TakeoutError(
                f"{path} does not look like a Google Takeout Keep export (no notes found)"
            )

        notes: list[KeepNote] = []
        for json_file in sorted(notes_dir.glob("*.json")):
            note = parse_keep_note(json_file)
            if note:
                notes.append(note)
        yield notes
    finally:
        if tmp is not None:
            tmp.cleanup()


def _resolve_keep_dir(path: Path) -> Path:
    """Accept the Keep folder itself or a Takeout root containing Keep/.

    Handles both layouts: legacy nests notes under `Keep/Notes/`, modern puts
    note JSONs directly in `Keep/`.
    """
    if not path.is_dir():
        return path
    inner = path / "Keep"
    if inner.is_dir() and (_notes_dir(inner) is not None):
        return inner
    return path


def _notes_dir(keep_dir: Path) -> Path | None:
    """Where note JSONs live inside a Keep dir: `Keep/Notes/` (legacy) or the
    Keep dir itself (modern). Legacy wins when both exist."""
    if not keep_dir.is_dir():
        return None
    legacy = keep_dir / "Notes"
    if legacy.is_dir():
        return legacy
    if any(keep_dir.glob("*.json")):
        return keep_dir
    return None


def _extract_zip(zip_path: Path, dest: Path) -> Path:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            keep_dir = _find_keep_dir_zip(zf)
            if keep_dir is None:
                raise TakeoutError(f"no 'Keep' folder found inside {zip_path}")
            _safe_extract(zf, dest)
            return dest / keep_dir
    except zipfile.BadZipFile as e:
        raise TakeoutError(f"not a valid zip archive: {zip_path}") from e


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract a zip without writing files outside `dest` (Zip Slip)."""
    dest = dest.resolve()
    for info in zf.infolist():
        name = info.filename
        if not name or name.startswith("/") or name.startswith("\\"):
            raise TakeoutError(f"unsafe path in zip: {name!r}")
        target = dest / name
        if not _is_inside(dest, target):
            raise TakeoutError(f"unsafe path in zip: {name!r}")
        if info.is_dir() or name.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def _find_keep_dir_zip(zf: zipfile.ZipFile) -> Path | None:
    """Locate the Keep dir inside a Takeout zip (legacy or modern layout)."""
    names = zf.namelist()
    legacy = [n for n in names if "/Notes/" in n]
    if legacy:
        first = legacy[0]
        return Path(first[: first.index("/Notes/")])
    # Modern: JSONs sit directly in the Keep dir; pick the dir holding them.
    json_dirs = {Path(n).parent for n in names if n.endswith(".json")}
    keep_dirs = sorted(d for d in json_dirs if d.name == "Keep")
    if keep_dirs:
        return keep_dirs[0]
    return None
