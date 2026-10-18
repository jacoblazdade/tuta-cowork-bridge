# SPDX-License-Identifier: AGPL-3.0-or-later
"""ASGI guard in front of the MCP Streamable HTTP app.

Order of checks for every HTTP request:
  1. X-API-Key, constant-time compare -> 401 (nothing else is reachable unauthenticated);
     repeated failures -> 429 lockout for unauthenticated requests
  2. rate limit for authenticated requests (token bucket) -> 429
  3. path must be the MCP path -> 404
  4. Origin, if present, must be allowlisted -> 403
  5. body size cap -> 413
  6. JSON-RPC tools/call names must be in the allowlist -> 403 (independent of the
     MCP layer's own registry; defence in depth)
Request logs carry method, path, status and timing only: never headers or bodies.
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from collections import deque
from typing import Any, Callable

log = logging.getLogger("tuta_cowork_bridge.http")


class TokenBucket:
    def __init__(self, per_minute: int, burst: int | None = None, clock: Callable[[], float] = time.monotonic):
        self.rate = per_minute / 60.0
        self.capacity = float(burst or max(10, per_minute // 4))
        self.tokens = self.capacity
        self.clock = clock
        self.updated = clock()

    def take(self) -> bool:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class AuthFailureLimiter:
    """After `threshold` failed authentications within `window` s, refuse
    unauthenticated attempts for `lockout` s (protects logs and CPU, not the key:
    a 256-bit key is not brute-forceable anyway)."""

    def __init__(self, threshold=20, window=60.0, lockout=300.0, clock=time.monotonic):
        self.threshold, self.window, self.lockout, self.clock = threshold, window, lockout, clock
        self.failures: deque[float] = deque()
        self.locked_until = 0.0

    def locked(self) -> bool:
        return self.clock() < self.locked_until

    def record(self) -> None:
        now = self.clock()
        self.failures.append(now)
        while self.failures and now - self.failures[0] > self.window:
            self.failures.popleft()
        if len(self.failures) >= self.threshold:
            self.locked_until = now + self.lockout
            self.failures.clear()
            log.warning("too many failed authentications; refusing unauthenticated requests for %ds", int(self.lockout))


async def _send_json(send, status: int, body: dict, extra_headers: list | None = None) -> None:
    data = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode()),
               (b"cache-control", b"no-store")] + (extra_headers or [])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": data})


def _rpc_tool_names(payload: Any) -> list[str] | None:
    """Tool names in tools/call requests; None if the payload is not JSON-RPC-shaped."""
    msgs = payload if isinstance(payload, list) else [payload]
    names = []
    for m in msgs:
        if not isinstance(m, dict):
            return None
        if m.get("method") == "tools/call":
            params = m.get("params")
            name = params.get("name") if isinstance(params, dict) else None
            names.append(name if isinstance(name, str) else "")
    return names


class Guard:
    def __init__(self, app, *, api_key: str | Callable[[], str], mcp_path: str, allowed_tools: frozenset[str],
                 allowed_origins: tuple[str, ...], max_body: int, rate_per_minute: int,
                 clock: Callable[[], float] = time.monotonic):
        self.app = app
        self._api_key = api_key
        self.mcp_path = mcp_path.rstrip("/")
        self.allowed_tools = allowed_tools
        self.allowed_origins = {o.rstrip("/") for o in allowed_origins}
        self.max_body = max_body
        self.bucket = TokenBucket(rate_per_minute, clock=clock)
        self.auth_failures = AuthFailureLimiter(clock=clock)

    def _key(self) -> bytes:
        key = self._api_key() if callable(self._api_key) else self._api_key
        return key.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":  # no websockets
            return
        start = time.monotonic()
        status_holder = {"status": 0}

        async def send_logged(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self._handle(scope, receive, send_logged)
        finally:
            log.info("%s %s -> %s (%.0f ms)", scope.get("method"), scope.get("path"), status_holder["status"],
                     (time.monotonic() - start) * 1000)

    async def _handle(self, scope, receive, send):
        headers = {k.decode("latin-1").lower(): v for k, v in scope.get("headers", [])}

        presented = headers.get("x-api-key", b"")
        if not hmac.compare_digest(presented, self._key()):
            if self.auth_failures.locked():
                return await _send_json(send, 429, {"error": "rate limited"}, [(b"retry-after", b"60")])
            self.auth_failures.record()
            return await _send_json(send, 401, {"error": "unauthorized"})

        # Only authenticated traffic draws from the bucket, so unauthenticated
        # floods cannot starve the legitimate client.
        if not self.bucket.take():
            return await _send_json(send, 429, {"error": "rate limited"}, [(b"retry-after", b"10")])

        path = scope.get("path", "")
        if path.rstrip("/") != self.mcp_path:
            return await _send_json(send, 404, {"error": "not found"})

        origin = headers.get("origin")
        if origin is not None and origin.decode("latin-1").rstrip("/") not in self.allowed_origins:
            return await _send_json(send, 403, {"error": "origin not allowed"})

        if scope.get("method") != "POST":
            return await self.app(scope, receive, send)

        declared = headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_body:
                    return await _send_json(send, 413, {"error": "request too large"})
            except ValueError:
                return await _send_json(send, 400, {"error": "bad content-length"})

        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > self.max_body:
                return await _send_json(send, 413, {"error": "request too large"})
            chunks.append(body)
            if not message.get("more_body"):
                break
        body = b"".join(chunks)

        try:
            names = _rpc_tool_names(json.loads(body)) if body else []
        except (ValueError, UnicodeDecodeError):
            names = []  # malformed JSON: let the MCP layer produce its own parse error
        for name in names or []:
            if name not in self.allowed_tools:
                log.warning("blocked call to non-allowlisted tool")
                return await _send_json(send, 403, {"jsonrpc": "2.0", "id": None,
                                                    "error": {"code": -32601, "message": "tool not available (read-only bridge)"}})

        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)
