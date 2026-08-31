"""Tests for the CLI entry point."""

import pytest

from keep_to_memos.__main__ import main
from keep_to_memos.client import MemosClient


@pytest.fixture(autouse=True)
def _clean_memos_env(monkeypatch):
    monkeypatch.delenv("MEMOS_TOKEN", raising=False)
    monkeypatch.delenv("MEMOS_URL", raising=False)


class TestArgs:
    def test_missing_source_exits(self, tmp_path):
        with pytest.raises(SystemExit) as e:
            main(["--token", "t", str(tmp_path / "nope")])
        assert e.value.code == 2  # argparse error

    def test_token_required(self, takeout_dir):
        with pytest.raises(SystemExit) as e:
            main([str(takeout_dir)])
        assert e.value.code == 2

    def test_token_from_env(self, takeout_dir, monkeypatch, capsys):
        monkeypatch.setenv("MEMOS_TOKEN", "from-env")
        monkeypatch.setattr(MemosClient, "whoami", lambda self: {})
        rc = main(["--dry-run", str(takeout_dir)])
        assert rc == 0
        assert capsys.readouterr().out.count("DRY-RUN") == 5

    def test_version(self, capsys):
        from keep_to_memos import __version__

        with pytest.raises(SystemExit) as e:
            main(["--version"])
        assert e.value.code == 0
        assert __version__ in capsys.readouterr().out


class TestDryRun:
    def test_dry_run_needs_no_server(self, takeout_dir, capsys, monkeypatch):
        # whoami fails (no server) but dry-run proceeds anyway.
        from keep_to_memos.client import MemosError

        def boom(self):
            raise MemosError("cannot reach memos: refused")

        monkeypatch.setattr(MemosClient, "whoami", boom)
        rc = main(["--dry-run", "--token", "t", str(takeout_dir)])
        out = capsys.readouterr().out
        assert rc == 0
        assert out.count("DRY-RUN") == 5

    def test_no_server_non_dry_run_exits(self, takeout_dir, monkeypatch, capsys):
        from keep_to_memos.client import MemosError

        def boom(self):
            raise MemosError("cannot reach memos: refused")

        monkeypatch.setattr(MemosClient, "whoami", boom)
        rc = main(["--token", "t", str(takeout_dir)])
        assert rc == 1
        assert "cannot reach memos" in capsys.readouterr().err


class TestZipInput:
    def test_zip_dry_run(self, takeout_zip, capsys, monkeypatch):
        monkeypatch.setattr(MemosClient, "whoami", lambda self: {})
        rc = main(["--dry-run", "--token", "t", str(takeout_zip)])
        out = capsys.readouterr().out
        assert rc == 0
        assert out.count("DRY-RUN") == 5

    def test_zip_does_not_leave_extract_dir(self, takeout_zip, tmp_path, monkeypatch):
        import shutil
        z = tmp_path / "t.zip"
        shutil.copy(takeout_zip, z)
        monkeypatch.setattr(MemosClient, "whoami", lambda self: {})
        rc = main(["--dry-run", "--token", "t", str(z)])
        assert rc == 0
        assert not (tmp_path / "_keep_extract").exists()
