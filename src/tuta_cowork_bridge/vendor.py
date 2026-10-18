# SPDX-License-Identifier: AGPL-3.0-or-later
"""Makes the pinned upstream checkouts importable and pins their safety switches.

tutaproxy-public provides the `tuta` package (TutaClient, crypto) and
tutamcp-public the `tutamcp` package (mail decryption helpers). Both are git
submodules under vendor/, pinned to reviewed release commits. Nothing from
them is registered as an MCP tool: only this package's own read-only tools are.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

VENDOR_DIR = Path(os.environ.get("TCB_VENDOR_DIR") or Path(__file__).resolve().parents[2] / "vendor")
UPSTREAMS = {"tutaproxy-public": "tuta", "tutamcp-public": "tutamcp"}

# Upstream environment switches that must never be active in this process.
FORBIDDEN_ENV = {
    "TUTA_SKIP_HMAC": "disables ciphertext integrity (HMAC) verification in tuta.crypto",
}

_done = False


def setup() -> None:
    global _done
    if _done:
        return
    for name, why in FORBIDDEN_ENV.items():
        if os.environ.get(name):
            raise RuntimeError(f"{name} is set ({why}); refusing to start")
    # Only tutaproxy's run_proxy.py starts telemetry and this process never imports it;
    # pinning the switch off as well guards against future upstream changes.
    os.environ["TUTAPROXY_TELEMETRY"] = "false"
    for name, package in UPSTREAMS.items():
        path = VENDOR_DIR / name
        if not (path / package / "__init__.py").is_file():
            raise RuntimeError(f"missing upstream checkout {path}; run: git submodule update --init")
        # Appended, not prepended: upstream trees also contain generic module names
        # (server.py, run.py, tests/) that must never shadow anything else.
        if str(path) not in sys.path:
            sys.path.append(str(path))
    _done = True
