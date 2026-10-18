"""Logging with redaction applied to the final formatted line (message + traceback)."""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys

_PATTERNS = [
    (re.compile(r"(?i)(blobAccessToken|accessToken|access_token)([\"'=:\s]+)[A-Za-z0-9_\-.%]+"), r"\1\2<redacted>"),
    (re.compile(r"(?i)(x-api-key[\"'=:\s]+)[^\s\"',]+"), r"\1<redacted>"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "<email>"),
]
_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    if value and len(value) >= 6:
        _secrets.add(value)


def redact(text: str) -> str:
    for s in _secrets:
        text = text.replace(s, "<redacted>")
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    if log_file:
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stderr)  # never stdout: it is the stdio MCP channel
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Upstream libraries log identifiers and, at DEBUG, raw API payload samples.
    for noisy in ("tuta", "tutamcp", "mcp", "aiohttp", "httpx", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
