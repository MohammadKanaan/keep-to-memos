# Contributing

## Development Setup

```bash
uv sync
```

This installs the project and the dev toolchain into the virtual environment
managed by `uv`.

## Common Commands

```bash
uv run pytest
uv run ruff check
```

Run both before opening a pull request. If you change CLI behavior, also
verify `uv run keep-to-memos --help`.

The live validation scripts in `tests/live/` run against a real memos
instance, see the README for setup.

## Pull Requests

- Keep changes focused and minimal.
- Add or update tests for behavior changes.
- Update docs or examples when public-facing behavior changes.
- Prefer small commits with clear messages.