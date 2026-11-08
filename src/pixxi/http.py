"""Low-level HTTP access to the Pixxi Data API, shared by C6 (read) and C8 (write).

Implements what the official docs specify (docs/pixxi_openapi.yaml):
- auth header X-PIXXI-TOKEN
- response envelope {statusCode, message, data}
- rate limits: >= 2 s between read requests (writes exempt), 429 on excess
"""

from __future__ import annotations

import time
from typing import Any, Callable, Literal

import httpx

from src.config import Settings

TOKEN_HEADER = "X-PIXXI-TOKEN"


class PixxiError(RuntimeError):
    """A Pixxi call failed (HTTP error or non-200 statusCode in the envelope)."""

    def __init__(self, status_code: int, path: str, message: str = ""):
        super().__init__(f"Pixxi {path} failed with {status_code}: {message}".strip(": "))
        self.status_code = status_code
        self.path = path
        self.message = message


class PixxiHttp:
    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        headers = {}
        if settings.pixxi_token is not None:
            headers[TOKEN_HEADER] = settings.pixxi_token.get_secret_value()
        if client is None:
            client = httpx.Client(
                base_url=settings.pixxi_base_url,
                timeout=settings.pixxi_timeout_seconds,
            )
        client.headers.update(headers)
        self._client = client
        self._clock = clock
        self._sleep = sleep
        self._last_read: float | None = None
        self.calls: list[tuple[str, str]] = []  # (METHOD, path) for audit and tests

    # -- rate limiting -------------------------------------------------------
    def _space_reads(self) -> None:
        interval = self.settings.pixxi_min_read_interval
        if interval <= 0:
            return
        now = self._clock()
        if self._last_read is not None:
            wait = interval - (now - self._last_read)
            if wait > 0:
                self._sleep(wait)
        self._last_read = self._clock()

    # -- core request --------------------------------------------------------
    def request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        kind: Literal["read", "write"],
        json: Any = None,
        params: dict | None = None,
    ) -> Any:
        """Send a request and return the envelope's `data` field.

        Only GET and POST are possible here; PUT/PATCH/DELETE are never sent (README hard rules).
        """
        if method not in ("GET", "POST"):
            raise ValueError(f"HTTP method {method} is not allowed by the middleware")

        attempts = 0
        while True:
            if kind == "read":
                self._space_reads()
            self.calls.append((method, path))
            response = self._client.request(method, path, json=json, params=params)
            if response.status_code == 429 and attempts < self.settings.pixxi_max_retries_on_429:
                attempts += 1
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else 2.0 * attempts
                self._sleep(delay)
                continue
            break

        if not 200 <= response.status_code < 300:
            raise PixxiError(response.status_code, path, response.text[:200])
        try:
            body = response.json()
        except ValueError:
            return None
        if isinstance(body, dict) and "statusCode" in body:
            if body.get("statusCode") != 200:
                raise PixxiError(int(body.get("statusCode") or 0), path, str(body.get("message", "")))
            return body.get("data")
        return body

    def close(self) -> None:
        self._client.close()
