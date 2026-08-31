"""Tests for the Memos HTTP client, using a stubbed urlopen.

These pin the wire format against the memos gateway's protobuf-backed JSON:
camelCase bodies, snake_case update masks, query-param memoId, base64
attachment content, and the retry behavior.
"""

import io
import json

import pytest

import keep_to_memos.client as client_mod
from keep_to_memos.client import MemosClient, MemosError


class FakeResponse:
    def __init__(self, payload: bytes | str):
        self.payload = payload

    def read(self) -> bytes:
        if isinstance(self.payload, str):
            return self.payload.encode("utf-8")
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Recorder:
    """Stub for urllib.request: records requests, replays canned responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.timeouts = []

    def __call__(self, req, timeout=None):
        self.requests.append((req.method, req.full_url, req.data, dict(req.headers)))
        self.timeouts.append(timeout)
        payload = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return FakeResponse(payload)


def _make_client(**kwargs) -> MemosClient:
    kwargs.setdefault("write_delay", 0)
    return MemosClient("http://memos.test", "memos_pat_x", **kwargs)


def _http_error(req, code: int, body: bytes):
    return client_mod.urllib.error.HTTPError(
        req.full_url, code, "err", None, io.BytesIO(body)
    )


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: None)


class TestRequestBasics:
    def test_get_parses_json(self, monkeypatch):
        rec = _Recorder(['{"user": {"name": "u1"}}'])
        monkeypatch.setattr(client_mod.urllib.request, "urlopen", rec)
        c = _make_client()
        assert c.whoami() == {"name": "u1"}
        method, url, data, headers = rec.requests[0]
        assert (method, url) == ("GET", "http://memos.test/api/v1/auth/me")
        assert headers["Authorization"] == "Bearer memos_pat_x"
        assert data is None

    def test_post_body_is_json(self, monkeypatch):
        rec = _Recorder(["{}"])
        monkeypatch.setattr(client_mod.urllib.request, "urlopen", rec)
        c = _make_client()
        c._request("POST", "/api/v1/memos", {"content": "hi"})
        _, _, data, headers = rec.requests[0]
        assert json.loads(data) == {"content": "hi"}
        # urllib normalizes header keys to title case
        assert headers["Content-type"] == "application/json"

    def test_http_error_raises_memos_error(self, monkeypatch):
        # The error detail is the server's JSON "message" field, if present.
        err_body = json.dumps({"message": "boom"}).encode()

        def fake_urlopen(req, timeout=None):
            raise _http_error(req, 400, err_body)

        monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(MemosError, match="HTTP 400: boom"):
            _make_client()._request("GET", "/api/v1/auth/me")

    def test_urlopen_uses_timeout(self, monkeypatch):
        rec = _Recorder(["{}"])
        monkeypatch.setattr(client_mod.urllib.request, "urlopen", rec)
        _make_client()._request("GET", "/api/v1/auth/me")
        assert rec.timeouts == [30.0]

    def test_invalid_json_raises(self, monkeypatch):
        rec = _Recorder(["not-json"])
        monkeypatch.setattr(client_mod.urllib.request, "urlopen", rec)
        with pytest.raises(MemosError, match="invalid JSON"):
            _make_client()._request("GET", "/api/v1/auth/me")


class TestRetries:
    def test_5xx_retried_then_succeeds(self, monkeypatch, no_sleep):
        attempts = {"n": 0}

        def fake_urlopen(req, timeout=None):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise _http_error(req, 500, b"busy")
            return FakeResponse(b"{}")

        monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake_urlopen)
        _make_client()._request("GET", "/api/v1/auth/me")
        assert attempts["n"] == 3

    def test_5xx_exhausted_retries_raises(self, monkeypatch, no_sleep):
        def fake_urlopen(req, timeout=None):
            raise _http_error(req, 503, b"down")

        monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(MemosError, match="HTTP 503"):
            _make_client()._request("GET", "/api/v1/auth/me")

    def test_connection_error_raises_memos_error(self, monkeypatch, no_sleep):
        import urllib.error

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("refused")

        monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(MemosError, match="cannot reach memos"):
            _make_client()._request("GET", "/api/v1/auth/me")

    def test_whoami_dry_run_does_not_retry(self, monkeypatch, no_sleep):
        import urllib.error

        attempts = {"n": 0}

        def fake_urlopen(req, timeout=None):
            attempts["n"] += 1
            raise urllib.error.URLError("refused")

        monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(MemosError, match="cannot reach memos"):
            _make_client(dry_run=True).whoami()
        assert attempts["n"] == 1


class TestWireFormat:
    def test_memo_id_rides_as_query_param(self, monkeypatch):
        # memoId in the body would be silently dropped by protojson.
        rec = _Recorder(["{}"])
        monkeypatch.setattr(client_mod.urllib.request, "urlopen", rec)
        c = _make_client()
        c.create_memo("content", memo_id="keep-abc")
        _, url, data, _ = rec.requests[0]
        assert "memoId=keep-abc" in url
        assert "memoId" not in json.loads(data)

    def test_wrapped_response_unwrapped(self, monkeypatch):
        # Some gateway builds wrap request-level fields.
        rec = _Recorder([json.dumps({"memo": {"name": "memos/keep-abc"}})])
        monkeypatch.setattr(client_mod.urllib.request, "urlopen", rec)
        c = _make_client()
        assert c.create_memo("") == {"name": "memos/keep-abc"}

    def test_attachment_body_is_base64(self, monkeypatch):
        rec = _Recorder(["{}"])
        monkeypatch.setattr(client_mod.urllib.request, "urlopen", rec)
        c = _make_client()
        c.create_attachment("a.png", b"\x89PNG", "image/png")
        _, url, data, _ = rec.requests[0]
        assert url.endswith("/api/v1/attachments")
        body = json.loads(data)
        import base64
        assert base64.b64decode(body["content"]) == b"\x89PNG"

    def test_update_mask_snake_case_in_query(self, monkeypatch):
        # Bodies are camelCase, update_mask entries are snake_case.
        rec = _Recorder(["{}"])
        monkeypatch.setattr(client_mod.urllib.request, "urlopen", rec)
        c = _make_client()
        c.patch_memo("memos/keep-abc", {"content": "x"}, ["content", "update_time"])
        _, url, data, _ = rec.requests[0]
        assert "update_mask=content%2Cupdate_time" in url
        assert json.loads(data) == {"content": "x"}

    def test_set_memo_attachments_body(self, monkeypatch):
        rec = _Recorder(["{}"])
        monkeypatch.setattr(client_mod.urllib.request, "urlopen", rec)
        c = _make_client()
        c.set_memo_attachments("memos/keep-abc", ["attachments/1"])
        _, url, data, _ = rec.requests[0]
        assert url.endswith("/api/v1/memos/keep-abc/attachments")
        assert json.loads(data) == {"attachments": [{"name": "attachments/1"}]}


class TestThrottle:
    def test_throttle_sleeps_between_writes(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(client_mod.time, "sleep", sleeps.append)
        c = _make_client(write_delay=5.0)
        c._throttle()
        c._throttle()
        c._throttle()
        assert len(sleeps) == 2  # first call: no prior write

    def test_no_throttle_when_disabled(self, monkeypatch):
        monkeypatch.setattr(client_mod.time, "sleep", lambda s: None)
        c = _make_client(write_delay=0)
        c._throttle()
        assert c._last_write == 0.0
