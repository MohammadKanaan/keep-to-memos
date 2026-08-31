# keep-to-memos

[![CI](https://github.com/MohammadKanaan/keep-to-memos/actions/workflows/ci.yml/badge.svg)](https://github.com/MohammadKanaan/keep-to-memos/actions/workflows/ci.yml)

Imports a [Google Keep](https://keep.google.com) Takeout export into a
[Memos](https://usememos.com) instance through Memos' API. Stdlib only,
Python 3.10+.

What is imported:

- Images as embedded attachments
- Original created and edited timestamps
- Labels as `#tags`, title as heading, pinned, color as a `#color/*` tag
- Archived notes keep their archived state
- Checklists as markdown task lists

Each Keep note maps to a deterministic memo ID, so re-running the same export
skips what is already there. A memo left empty by a failed run is deleted and
retried.

## Usage

1. Export Keep from https://takeout.google.com (select Keep only). Download
   the zip, or unpack it.
2. Create a personal access token in Memos: Settings → Access Tokens.
3. Run:

```bash
uvx --from keep-to-memos keep-to-memos \
  --url https://your-memos-instance \
  --token memos_pat_... \
  /path/to/takeout-*.zip     # or the unpacked Takeout/Keep folder
```

Environment variables can be used instead of command-line flags:

- --token => MEMOS_TOKEN
- --url => MEMOS_URL

Install permanently with `uv tool install keep-to-memos` or
`pipx install keep-to-memos`.

### Flags

- `--dry-run` — print what would be imported, write nothing
- `--include-trashed` — import trashed notes (skipped by default)

## Behavior notes

- Trashed notes are skipped by default.
- Zip input is extracted to a temp directory and deleted afterwards.
- Writes are throttled (0.5s apart) and 5xx errors retried, since memos'
  SQLite write path returns `SQLITE_BUSY` under back-to-back writes.
- Attachments are created unbound, then attached via `SetMemoAttachments`.
  Bound-at-creation fails on some memos builds.
- The memos gateway is protobuf-backed. Request bodies use camelCase
  (`createTime`), `update_mask` entries use snake_case (`update_time`).

## Development

```bash
uv sync
uv run pytest
uv run ruff check
```

After changing files under `testdata/Takeout`, rebuild the zip fixture:

```bash
python3 testdata/make_zip.py
```

## License

MIT, see [LICENSE](LICENSE).
