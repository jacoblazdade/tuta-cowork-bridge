"""One long-lived Tuta session per process.

- Lazy login on first use; credentials are loaded only at login time.
- 401/440 from Tuta (expired session) -> drop the session, log in again, retry once.
- Transient network errors -> one retry.
- Failed logins back off exponentially so a wrong password or 2FA problem can
  never turn into a stream of login attempts against the account.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Awaitable, Callable

import aiohttp

from . import vendor

vendor.setup()
from tuta.api import Session, TutaAPIError, TutaClient  # noqa: E402

from .credentials import TutaCredentials  # noqa: E402

log = logging.getLogger(__name__)

LOGIN_TIMEOUT = 90
TRANSIENT = (aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError, asyncio.TimeoutError)


class LoginUnavailable(RuntimeError):
    """Login is paused after a recent failure."""

    def __init__(self, retry_in: int, reason: str):
        super().__init__(f"Tuta login paused for {retry_in}s after a failure ({reason})")
        self.retry_in = retry_in
        self.reason = reason


def _classify(exc: BaseException) -> tuple[str, int]:
    """(reason, backoff base seconds). Never includes server text or secrets."""
    if isinstance(exc, TutaAPIError):
        code = exc.status_code
        if code == 401:
            return "rejected credentials", 300
        if code == 429:
            return "rate limited by Tuta", 120
        if code == 474:
            return "client version rejected by Tuta (upstream update needed)", 1800
        if code == 0:
            msg = str(exc)
            if "TOTP" in msg or "2FA" in msg:
                return "second factor failed or missing", 300
            return "login protocol error", 300
        return f"Tuta HTTP {code}", 60
    if isinstance(exc, TRANSIENT) or isinstance(exc, OSError):
        return "network error", 15
    return type(exc).__name__, 60


class TutaSession:
    def __init__(
        self,
        load_credentials: Callable[[], TutaCredentials],
        client_factory: Callable[[], TutaClient] = TutaClient,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._load_credentials = load_credentials
        self._client_factory = client_factory
        self._clock = clock
        self._lock = asyncio.Lock()
        self._client: TutaClient | None = None
        self._session: Session | None = None
        self._mail_group_key: bytes | None = None
        self.generation = 0  # bumps on every new login; lets caches detect a fresh session
        self._failures = 0
        self._paused_until = 0.0
        self._pause_reason = ""
        self.last_login_at: float | None = None
        self.last_error: str | None = None

    # -- public -----------------------------------------------------------------

    async def run(self, fn: Callable[[TutaClient, Session, bytes], Awaitable[Any]],
                  retry_transient: bool = True) -> Any:
        """Runs fn with a live session. Expired sessions are always retried (the request was
        rejected, so nothing happened). Network errors are retried only when retry_transient:
        pass False for non-idempotent writes such as sending, where the first attempt may
        have reached Tuta before the connection failed."""
        client, session, mgk, gen = await self._ensure()
        try:
            return await fn(client, session, mgk)
        except TutaAPIError as e:
            if e.status_code not in (401, 440):
                raise
            log.info("Tuta session expired (HTTP %d); logging in again", e.status_code)
            await self._drop(gen, logout=False)
        except TRANSIENT as e:
            if not retry_transient:
                raise
            log.warning("transient Tuta error (%s); retrying once", type(e).__name__)
            await asyncio.sleep(1)
        client, session, mgk, _ = await self._ensure()
        return await fn(client, session, mgk)

    def status(self) -> dict[str, Any]:
        paused = max(0, int(self._paused_until - self._clock()))
        return {
            "logged_in": self._client is not None,
            "login_paused_seconds": paused,
            "last_error": self.last_error,
        }

    async def close(self) -> None:
        async with self._lock:
            await self._close_client(logout=True)

    # -- internals --------------------------------------------------------------

    async def _ensure(self) -> tuple[TutaClient, Session, bytes, int]:
        async with self._lock:
            if self._client is None:
                await self._login()
            assert self._client and self._session and self._mail_group_key
            return self._client, self._session, self._mail_group_key, self.generation

    async def _drop(self, generation: int, logout: bool) -> None:
        async with self._lock:
            if generation == self.generation:  # another task may already have re-logged in
                await self._close_client(logout=logout)

    async def _login(self) -> None:
        now = self._clock()
        if now < self._paused_until:
            raise LoginUnavailable(int(self._paused_until - now) + 1, self._pause_reason)
        creds = await asyncio.to_thread(self._load_credentials)
        client = self._client_factory()
        await client.__aenter__()
        try:
            # tuta.api reads the TOTP seed from os.environ; expose it only for the
            # duration of the login call and remove it again.
            if creds.totp_secret:
                os.environ["TUTA_TOTP_SECRET"] = creds.totp_secret
            try:
                session = await asyncio.wait_for(client.login(creds.email, creds.password), LOGIN_TIMEOUT)
            finally:
                os.environ.pop("TUTA_TOTP_SECRET", None)
            mgk = await client.get_mail_group_key(session)
        except BaseException as e:
            await client.__aexit__(None, None, None)
            if not isinstance(e, Exception):  # cancellation/shutdown is not a login failure
                raise
            reason, base = _classify(e)
            self._failures += 1
            delay = min(base * 2 ** (self._failures - 1), 3600)
            self._paused_until = self._clock() + delay
            self._pause_reason = reason
            self.last_error = reason
            log.error("Tuta login failed: %s; next attempt allowed in %ds", reason, delay)
            raise
        self._client, self._session, self._mail_group_key = client, session, mgk
        self.generation += 1
        self._failures = 0
        self._paused_until = 0.0
        self.last_login_at = time.time()
        self.last_error = None
        log.info("Tuta login ok")

    async def _close_client(self, logout: bool) -> None:
        client, session = self._client, self._session
        self._client = self._session = None
        self._mail_group_key = None
        if client is None:
            return
        if logout and session is not None:
            try:
                await asyncio.wait_for(client.logout(session), 15)
            except Exception as e:  # best effort; the session will expire server-side
                log.warning("Tuta logout failed: %s", type(e).__name__)
        await client.__aexit__(None, None, None)
