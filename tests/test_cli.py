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
    def test_dry_run_without_token_stays_offline(self, takeout_dir, capsys, monkeypatch):
        # No token, no server: dry-run must still parse and print.
        def boom(self):
            raise AssertionError("whoami must not be called without a token")

        monkeypatch.setattr(MemosClient, "whoami", boom)
        rc = main(["--dry-run", str(takeout_dir)])
        out = capsys.readouterr().out
        assert rc == 0
        assert out.count("DRY-RUN") == 5
        assert "skipping connection check" in out

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

    def test_idempotency_check_failure_exits_cleanly(
        self, takeout_dir, monkeypatch, capsys
    ):
        # migrate() re-raises MemosError when the idempotency GET fails
        # with something other than "not found" (e.g. a persistent 500).
        # The CLI must exit 1 with a message, not a traceback.
        from keep_to_memos.client import MemosError

        def boom(self, memo_name):
            raise MemosError(f"GET /api/v1/{memo_name} -> HTTP 500: busy")

        monkeypatch.setattr(MemosClient, "whoami", lambda self: {})
        monkeypatch.setattr(MemosClient, "get_memo", boom)
        rc = main(["--token", "t", str(takeout_dir)])
        assert rc == 1
        assert "HTTP 500: busy" in capsys.readouterr().err


class TestThrottleFlag:
    @staticmethod
    def _capture_client(monkeypatch):
        captured = {}

        class CapturingClient(MemosClient):
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)
                super().__init__(*args, **kwargs)

            def whoami(self):
                return {}

        monkeypatch.setattr("keep_to_memos.__main__.MemosClient", CapturingClient)
        return captured

    def test_throttle_on_by_default(self, takeout_dir, monkeypatch):
        captured = self._capture_client(monkeypatch)
        assert main(["--dry-run", "--token", "t", str(takeout_dir)]) == 0
        assert captured["write_delay"] == 0.5

    def test_no_throttle_disables_delay(self, takeout_dir, monkeypatch):
        captured = self._capture_client(monkeypatch)
        assert main(["--dry-run", "--no-throttle", "--token", "t", str(takeout_dir)]) == 0
        assert captured["write_delay"] == 0.0


class TestInteractive:
    @staticmethod
    def _capture(monkeypatch):
        """Capture the URL/token passed to MemosClient; stub out the import."""
        captured = {}

        class CapturingClient(MemosClient):
            def __init__(self, url, token, **kwargs):
                captured["url"] = url
                captured["token"] = token

            def whoami(self):
                return {}

        monkeypatch.setattr("keep_to_memos.__main__.MemosClient", CapturingClient)
        monkeypatch.setattr("keep_to_memos.__main__.migrate", lambda *a, **k: 0)
        monkeypatch.setattr("keep_to_memos.__main__._is_interactive", lambda: True)
        return captured

    def test_prompts_for_missing_token(self, takeout_dir, monkeypatch):
        captured = self._capture(monkeypatch)
        monkeypatch.setattr("getpass.getpass", lambda prompt: "prompted-token")
        assert main(["--url", "http://m.example", str(takeout_dir)]) == 0
        assert captured["token"] == "prompted-token"

    def test_prompts_for_missing_url_accepting_default(self, takeout_dir, monkeypatch):
        captured = self._capture(monkeypatch)
        monkeypatch.setattr("builtins.input", lambda prompt: "")
        monkeypatch.setattr("getpass.getpass", lambda prompt: "tok")
        assert main([str(takeout_dir)]) == 0
        assert captured["url"] == "http://localhost:5230"

    def test_prompted_url_value_used(self, takeout_dir, monkeypatch):
        captured = self._capture(monkeypatch)
        monkeypatch.setattr("builtins.input", lambda prompt: "https://m.example")
        monkeypatch.setattr("getpass.getpass", lambda prompt: "tok")
        assert main([str(takeout_dir)]) == 0
        assert captured["url"] == "https://m.example"

    def test_env_values_skip_prompts(self, takeout_dir, monkeypatch):
        captured = self._capture(monkeypatch)
        monkeypatch.setenv("MEMOS_URL", "https://env.example")
        monkeypatch.setenv("MEMOS_TOKEN", "env-token")

        def fail(prompt):
            raise AssertionError("must not prompt when env is set")

        monkeypatch.setattr("builtins.input", fail)
        monkeypatch.setattr("getpass.getpass", fail)
        assert main([str(takeout_dir)]) == 0
        assert (captured["url"], captured["token"]) == ("https://env.example", "env-token")

    def test_empty_token_entered_errors(self, takeout_dir, monkeypatch, capsys):
        self._capture(monkeypatch)
        monkeypatch.setattr("builtins.input", lambda prompt: "")
        monkeypatch.setattr("getpass.getpass", lambda prompt: "")
        with pytest.raises(SystemExit) as e:
            main([str(takeout_dir)])
        assert e.value.code == 2
        assert "required" in capsys.readouterr().err

    def test_prompt_eof_exits_cleanly(self, takeout_dir, monkeypatch, capsys):
        # Ctrl-D at a prompt should abort with a message, not a traceback.
        self._capture(monkeypatch)

        def eof(prompt):
            raise EOFError

        monkeypatch.setattr("builtins.input", eof)
        rc = main([str(takeout_dir)])
        assert rc == 1
        assert "aborting" in capsys.readouterr().err


class TestSourcePrompt:
    @staticmethod
    def _capture_cwd(monkeypatch, takeout_dir):
        """Make _is_interactive true and record the cwd the import ran in."""
        monkeypatch.setattr("keep_to_memos.__main__._is_interactive", lambda: True)
        monkeypatch.setattr(MemosClient, "whoami", lambda self: {})
        captured = {}

        def fake_load(path):
            captured["source"] = path

            class Ctx:
                def __enter__(self):
                    return []

                def __exit__(self, *exc):
                    return False

            return Ctx()

        monkeypatch.setattr("keep_to_memos.__main__.load_takeout", fake_load)
        return captured

    def test_omitted_source_is_prompted(self, takeout_dir, monkeypatch):
        captured = self._capture_cwd(monkeypatch, takeout_dir)
        monkeypatch.setattr("builtins.input", lambda prompt: str(takeout_dir))
        rc = main(["--dry-run"])
        assert rc == 0
        assert captured["source"] == takeout_dir

    def test_blank_source_errors(self, monkeypatch, capsys):
        self._capture_cwd(monkeypatch, None)
        monkeypatch.setattr("builtins.input", lambda prompt: "")
        with pytest.raises(SystemExit) as e:
            main(["--dry-run"])
        assert e.value.code == 2
        assert "source path is required" in capsys.readouterr().err

    def test_non_interactive_omitted_source_errors(self, monkeypatch, capsys):
        monkeypatch.setattr("keep_to_memos.__main__._is_interactive", lambda: False)
        with pytest.raises(SystemExit) as e:
            main(["--dry-run"])
        assert e.value.code == 2
        assert "required: source" in capsys.readouterr().err


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
