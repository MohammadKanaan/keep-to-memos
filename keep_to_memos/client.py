"""Minimal stdlib-only HTTP client for the Memos v1 API.

The Memos REST gateway is protobuf-backed and speaks protojson (camelCase
field names). Two casing gotchas this client encodes:

- Request/response bodies use camelCase (`createTime`, `personalAccessTokens`);
  snake_case body fields are silently dropped, not rejected.
- `update_mask` query-parameter entries use snake_case (`update_time`) even
  though the corresponding body field is camelCase.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class MemosError(Exception):
    """An API request failed (non-retryable, or retries exhausted)."""


class MemosClient:
    def __init__(
        self,
        url: str,
        token: str,
        dry_run: bool = False,
        write_delay: float = 0.5,
        timeout: float = 30.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.dry_run = dry_run
        # Brief pause between sequential writes: memos' SQLite write path can
        # return SQLITE_BUSY when requests arrive back-to-back.
        self.write_delay = write_delay
        self.timeout = timeout
        self._last_write = 0.0

    def _throttle(self) -> None:
        if self.write_delay <= 0:
            return
        elapsed = time.monotonic() - self._last_write
        if elapsed < self.write_delay:
            time.sleep(self.write_delay - elapsed)
        self._last_write = time.monotonic()

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None, retries: int = 3
    ) -> dict[str, Any]:
        data = None
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        for attempt in range(retries + 1):
            req = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = resp.read().decode("utf-8")
                try:
                    return json.loads(payload) if payload.strip() else {}
                except json.JSONDecodeError as e:
                    raise MemosError(f"{method} {path} -> invalid JSON from memos") from e
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")
                try:
                    parsed = json.loads(detail)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    detail = parsed.get("message", detail)
                if e.code >= 500 and attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise MemosError(f"{method} {path} -> HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                reason = getattr(e, "reason", e)
                raise MemosError(f"{method} {path} -> cannot reach memos: {reason}") from e
        raise MemosError(f"{method} {path} -> exhausted retries")

    def whoami(self) -> dict[str, Any]:
        # Dry-run still tries a connection check, but should not sit through
        # the write-path retry loop when the instance is down.
        retries = 0 if self.dry_run else 3
        return self._request("GET", "/api/v1/auth/me", retries=retries).get("user", {})

    def create_attachment(self, filename: str, content: bytes, mime_type: str) -> dict[str, Any]:
        """Create an attachment (unbound). Bind it to a memo with
        `set_memo_attachments()`."""
        self._throttle()
        # The gateway maps the HTTP body directly to the Attachment message.
        body = {
            "filename": filename,
            "type": mime_type,
            "content": base64.b64encode(content).decode("ascii"),
        }
        return self._request("POST", "/api/v1/attachments", body)

    def set_memo_attachments(self, memo_name: str, attachment_names: list[str]) -> dict[str, Any]:
        """Replace the full attachment set of a memo."""
        self._throttle()
        return self._request(
            "PATCH",
            f"/api/v1/{memo_name}/attachments",
            {"attachments": [{"name": n} for n in attachment_names]},
        )

    def create_memo(
        self,
        content: str,
        created_ts: int | None = None,
        updated_ts: int | None = None,
        state: str = "NORMAL",
        memo_id: str | None = None,
    ) -> dict[str, Any]:
        self._throttle()
        memo: dict[str, Any] = {"content": content, "state": state, "visibility": "PRIVATE"}
        if created_ts is not None:
            memo["createTime"] = rfc3339(created_ts)
        if updated_ts is not None:
            memo["updateTime"] = rfc3339(updated_ts)
        # `memo_id` is a CreateMemoRequest sibling of the body-mapped `memo`
        # field, so it must travel as a query parameter — in the body it is
        # silently dropped (protojson discards unknown fields).
        path = "/api/v1/memos"
        if memo_id:
            path += "?" + urllib.parse.urlencode({"memoId": memo_id})
        created = self._request("POST", path, memo)
        # Some gateway builds wrap request-level fields; fall back to the
        # body shape we sent.
        if "name" not in created and "memo" in created:
            created = created["memo"]
        return created

    def patch_memo(
        self, memo_name: str, fields: dict[str, Any], update_mask: list[str]
    ) -> dict[str, Any]:
        self._throttle()
        # The body maps to `memo`; the update mask travels via query and uses
        # snake_case paths (see module docstring).
        query = urllib.parse.urlencode({"update_mask": ",".join(update_mask)})
        return self._request("PATCH", f"/api/v1/{memo_name}?{query}", fields)

    def get_memo(self, memo_name: str) -> dict[str, Any]:
        return self._request("GET", "/api/v1/" + memo_name)

    def delete_memo(self, memo_name: str) -> dict[str, Any]:
        self._throttle()
        return self._request("DELETE", "/api/v1/" + memo_name)


def rfc3339(ts: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
