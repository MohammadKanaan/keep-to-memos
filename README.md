# keep-to-memos

[![CI](https://github.com/MohammadKanaan/keep-to-memos/actions/workflows/ci.yml/badge.svg)](https://github.com/MohammadKanaan/keep-to-memos/actions/workflows/ci.yml)

Moves a [Google Keep](https://keep.google.com) Takeout export into a
[Memos](https://usememos.com) instance over HTTP. Stdlib only, Python 3.10+.

## Usage

1. Download a Takeout export from https://takeout.google.com.
2. Create a personal access token in Memos under Settings -> Access Tokens.

Then run the importer:

```bash
uvx --from keep-to-memos keep-to-memos \
  --url https://your-memos-instance \
  --token memos_pat_... \
  /path/to/takeout-*.zip
```

The source argument takes the zip or an unpacked `Takeout/Keep` folder. Set
`MEMOS_URL` and `MEMOS_TOKEN` instead of the flags to keep the token out of your
shell history. `--url` defaults to `http://localhost:5230`.

Run from a terminal and the importer prompts for whatever is missing, with the
token hidden. Non-interactive runs fail fast. To install permanently, use
`uv tool install keep-to-memos` or `pipx install keep-to-memos`.

Flags:

- `--dry-run` prints what would be imported. Needs no token or server.
- `--include-trashed` also imports trashed notes. Off by default.
- `--no-throttle` skips the 0.5s pause between writes. Only safe on Postgres or
  MySQL; SQLite returns `SQLITE_BUSY` under back-to-back writes.

## What it imports

- Images as embedded attachments
- Original created and edited timestamps
- Labels as `#tags`, title as a heading, pinned state, color as a `#color/*` tag
- Archived notes stay archived
- Checklists as markdown task lists

Re-running is safe. Each note maps to a deterministic memo ID, so the second run
skips what exists; empty memos from a failed run are deleted and retried.

## Notes

- Zip input is extracted to a temp dir and deleted afterward.
- Writes are spaced 0.5s apart with 5xx retries; memos' SQLite path returns
  `SQLITE_BUSY` under back-to-back writes.
- Attachments are created unbound, then attached via `SetMemoAttachments`;
  binding at creation fails on some builds.
- The gateway is protobuf-backed: bodies use camelCase (`createTime`),
  `update_mask` snake_case (`update_time`).

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). After editing `testdata/Takeout`, rebuild
the fixture with `python3 testdata/make_zip.py`.

## License

MIT, see [LICENSE](LICENSE).
