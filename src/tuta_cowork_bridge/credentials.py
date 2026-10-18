"""Secret storage: macOS Keychain (default) or a strictly-permissioned file.

Item names (Keychain accounts / file keys):
    TUTA_EMAIL, TUTA_PASSWORD, TUTA_TOTP_SECRET (optional), TCB_API_KEY

Secrets never travel through argv: reads use `security ... -w` (value on stdout),
writes of generated values go through `security -i` on stdin, and user-typed
values are entered at `security`'s own hidden prompt (scripts/setup.sh).
"""

from __future__ import annotations

import os
import re
import secrets
import stat
import subprocess
from dataclasses import dataclass

from .config import Settings

SECURITY_BIN = "/usr/bin/security"
ITEM_NAMES = ("TUTA_EMAIL", "TUTA_PASSWORD", "TUTA_TOTP_SECRET", "TCB_API_KEY")
_NOT_FOUND = 44  # errSecItemNotFound exit status of security(1)
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


class CredentialError(RuntimeError):
    """Raised without ever including a secret value in the message."""


@dataclass(frozen=True, repr=False)
class TutaCredentials:
    email: str
    password: str
    totp_secret: str | None

    def __repr__(self) -> str:  # never print secrets, even in tracebacks
        return "TutaCredentials(<redacted>)"


class _Keychain:
    def __init__(self, service: str):
        self.service = service

    def get(self, name: str) -> str | None:
        try:
            r = subprocess.run(
                [SECURITY_BIN, "find-generic-password", "-s", self.service, "-a", name, "-w"],
                capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise CredentialError(f"keychain read of {name} failed: {type(e).__name__}") from None
        if r.returncode == _NOT_FOUND:
            return None
        if r.returncode != 0:
            # 36/51 etc.: keychain locked or interaction not allowed (e.g. no GUI login yet)
            raise CredentialError(f"keychain read of {name} failed (security exit {r.returncode}; is the login keychain unlocked?)")
        return r.stdout.rstrip("\n")

    def put_generated(self, name: str, value: str) -> None:
        if not _SAFE_VALUE.match(value):
            raise CredentialError("refusing to store a value with unexpected characters")
        cmd = f"add-generic-password -U -s {self.service} -a {name} -l {self.service} -w {value}\n"
        r = subprocess.run([SECURITY_BIN, "-i"], input=cmd, capture_output=True, text=True, timeout=20)
        if r.returncode != 0 or "error" in r.stderr.lower():
            raise CredentialError(f"keychain write of {name} failed (security exit {r.returncode})")


class _File:
    def __init__(self, path: str):
        self.path = path

    def _load(self) -> dict[str, str]:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            raise CredentialError(f"credentials file not found: {self.path}") from None
        if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise CredentialError(f"credentials file {self.path} must not be group/world accessible (chmod 600)")
        if st.st_uid != os.getuid():
            raise CredentialError(f"credentials file {self.path} must be owned by the service user")
        values: dict[str, str] = {}
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
        return values

    def get(self, name: str) -> str | None:
        return self._load().get(name) or None

    def put_generated(self, name: str, value: str) -> None:
        raise CredentialError("file backend is read-only; add the value to the file yourself")


class CredentialStore:
    def __init__(self, settings: Settings):
        if settings.credential_backend == "file":
            self._backend: _Keychain | _File = _File(settings.credentials_file or "")
        else:
            self._backend = _Keychain(settings.keychain_service)

    def get(self, name: str) -> str | None:
        if name not in ITEM_NAMES:
            raise CredentialError(f"unknown credential item {name}")
        return self._backend.get(name)

    def tuta(self) -> TutaCredentials:
        email, password = self.get("TUTA_EMAIL"), self.get("TUTA_PASSWORD")
        if not email or not password:
            raise CredentialError("TUTA_EMAIL / TUTA_PASSWORD not configured; run scripts/setup.sh")
        totp = self.get("TUTA_TOTP_SECRET")
        return TutaCredentials(email=email, password=password, totp_secret=totp or None)

    def api_key(self) -> str:
        key = self.get("TCB_API_KEY")
        if not key or len(key) < 32:
            raise CredentialError("TCB_API_KEY missing or too short; run scripts/setup.sh")
        return key

    def generate_api_key(self) -> None:
        """Creates/rotates the MCP API key (256 bits). The value is never printed."""
        self._backend.put_generated("TCB_API_KEY", secrets.token_urlsafe(32))

    def status(self) -> dict[str, bool]:
        """Which items exist — booleans only, never values."""
        out = {}
        for name in ITEM_NAMES:
            try:
                out[name] = bool(self.get(name))
            except CredentialError:
                out[name] = False
        return out
