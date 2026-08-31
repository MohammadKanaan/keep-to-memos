# Releasing keep-to-memos

This project is set up to publish to PyPI from GitHub Actions using PyPI Trusted
Publishing.

## Before the first public release

The GitHub repo starts private. Before tagging `v1.0.0`:

1. Push `master`.
2. Make the repository public.
3. Set the description to "Import a Google Keep Takeout export into a Memos instance"
   and topics `google-keep`, `memos`, `takeout`, `migration`, `python`.
4. Turn on private vulnerability reporting (Settings → Code security).
5. Disable the wiki if it is still on.

Then complete the PyPI trusted-publisher setup below.

## One-Time Setup

1. Create a PyPI account for the maintainer if you do not already have one.
2. In PyPI, go to `Account settings` -> `Publishing`.
3. Add a pending trusted publisher with:
   - PyPI project name: `keep-to-memos`
   - Owner: `MohammadKanaan`
   - Repository: `keep-to-memos`
   - Workflow file: `publish.yml`
   - Environment: `pypi`
4. In GitHub, create an environment named `pypi`.
5. Require manual approval for that environment before the publish job runs.

## Release Checklist

```bash
uv sync
uv run pytest
uv run ruff check .
uv build
uvx twine check dist/*
```

## Publish a Release

1. Update `__version__` in `keep_to_memos/__init__.py`.
2. Commit the release changes.
3. Create and push a tag that matches the version:

```bash
git tag v1.0.0
git push origin v1.0.0
```

4. Approve the `pypi` environment in GitHub when the `Publish` workflow starts.
5. Confirm the release at `https://pypi.org/project/keep-to-memos/`.

## Notes

- `dist/` should be treated as build output; rebuild it for every release.
- If you want to dry-run the pipeline before the first public release, publish to
  TestPyPI manually instead of tagging production.