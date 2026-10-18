# SPDX-License-Identifier: AGPL-3.0-or-later
"""Non-secret runtime settings, read from TCB_* environment variables.

Secrets (Tuta credentials, the MCP API key) never come from here; see credentials.py.
"""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass

DEFAULT_ORIGINS = ("https://claude.ai", "https://claude.com")
WRITE_GROUPS = ("organize", "folders", "send")
SOURCE_URL = "https://github.com/jacoblazdade/tuta-cowork-bridge"


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(v.strip() for v in (value or "").split(",") if v.strip())


def _int(env: dict, name: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(name)
    if raw in (None, ""):
        return default
    value = int(raw)
    if not lo <= value <= hi:
        raise ValueError(f"{name}={value} outside {lo}..{hi}")
    return value


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8080
    mcp_path: str = "/mcp"
    public_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = DEFAULT_ORIGINS
    credential_backend: str = "keychain"
    keychain_service: str = "tuta-cowork-bridge"
    credentials_file: str | None = None
    rate_per_minute: int = 120
    max_body_bytes: int = 256 * 1024
    tool_timeout: int = 90
    max_concurrent_tools: int = 4
    log_level: str = "INFO"
    source_url: str = SOURCE_URL
    allow_non_loopback: bool = False
    write_groups: tuple[str, ...] = ()
    send_allowlist: tuple[str, ...] = ("me",)
    daily_write_limit: int = 500
    daily_send_limit: int = 20
    audit_file: str | None = None

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        e = os.environ if env is None else env
        s = cls(
            host=e.get("TCB_HOST", "127.0.0.1"),
            port=_int(e, "TCB_PORT", 8080, 1, 65535),
            mcp_path=e.get("TCB_MCP_PATH", "/mcp"),
            public_hosts=_csv(e.get("TCB_PUBLIC_HOSTS")),
            allowed_origins=_csv(e.get("TCB_ALLOWED_ORIGINS")) or DEFAULT_ORIGINS,
            credential_backend=e.get("TCB_CREDENTIAL_BACKEND", "keychain"),
            keychain_service=e.get("TCB_KEYCHAIN_SERVICE", "tuta-cowork-bridge"),
            credentials_file=e.get("TCB_CREDENTIALS_FILE") or None,
            rate_per_minute=_int(e, "TCB_RATE_PER_MINUTE", 120, 1, 10_000),
            max_body_bytes=_int(e, "TCB_MAX_BODY_BYTES", 256 * 1024, 1024, 4 * 1024 * 1024),
            tool_timeout=_int(e, "TCB_TOOL_TIMEOUT", 90, 5, 600),
            max_concurrent_tools=_int(e, "TCB_MAX_CONCURRENT_TOOLS", 4, 1, 32),
            log_level=e.get("TCB_LOG_LEVEL", "INFO").upper(),
            source_url=e.get("TCB_SOURCE_URL", SOURCE_URL),
            allow_non_loopback=e.get("TCB_ALLOW_NON_LOOPBACK") == "1",
            write_groups=tuple(g.lower() for g in _csv(e.get("TCB_WRITE_TOOLS"))),
            send_allowlist=tuple(a.lower() for a in _csv(e.get("TCB_SEND_ALLOWLIST"))) or ("me",),
            daily_write_limit=_int(e, "TCB_DAILY_WRITE_LIMIT", 500, 1, 100_000),
            daily_send_limit=_int(e, "TCB_DAILY_SEND_LIMIT", 20, 1, 1000),
            audit_file=e.get("TCB_AUDIT_FILE") or None,
        )
        s.validate()
        return s

    def validate(self) -> None:
        if not self.mcp_path.startswith("/") or self.mcp_path == "/":
            raise ValueError("TCB_MCP_PATH must be a non-root absolute path")
        if self.credential_backend not in ("keychain", "file"):
            raise ValueError("TCB_CREDENTIAL_BACKEND must be 'keychain' or 'file'")
        if self.credential_backend == "file" and not self.credentials_file:
            raise ValueError("TCB_CREDENTIALS_FILE is required with the file backend")
        if not is_loopback(self.host) and not self.allow_non_loopback:
            raise ValueError(
                f"refusing to listen on non-loopback address {self.host!r}; "
                "put a TLS reverse proxy (e.g. Tailscale Funnel) in front of a loopback listener, "
                "or set TCB_ALLOW_NON_LOOPBACK=1 inside an isolated container network"
            )
        for origin in self.allowed_origins:
            if not origin.startswith("https://"):
                raise ValueError(f"allowed origin must be https: {origin!r}")
        unknown = set(self.write_groups) - set(WRITE_GROUPS)
        if unknown:
            raise ValueError(f"TCB_WRITE_TOOLS: unknown group(s) {sorted(unknown)}; valid: {', '.join(WRITE_GROUPS)}")
        for entry in self.send_allowlist:
            if entry not in ("me", "*") and not re.fullmatch(r"(@|[^@\s]+@)[a-z0-9.-]+\.[a-z]{2,}", entry):
                raise ValueError(f"TCB_SEND_ALLOWLIST entry {entry!r}: use me, *, name@domain or @domain")
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ValueError("TCB_LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")

    @property
    def allowed_hosts(self) -> list[str]:
        hosts = [f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"]
        for h in self.public_hosts:
            hosts += [h, f"{h}:443", f"{h}:8443", f"{h}:10000"]
        return hosts
