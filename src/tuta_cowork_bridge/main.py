# SPDX-License-Identifier: AGPL-3.0-or-later
"""Command line entry point.

    tuta-cowork-bridge serve        Streamable HTTP on TCB_HOST:TCB_PORT (loopback only by default)
    tuta-cowork-bridge stdio        stdio transport for local clients (Claude Code / Desktop)
    tuta-cowork-bridge check        configuration self-check; prints no secret values
    tuta-cowork-bridge live-check   logs in to Tuta and prints counts only (no mail content)
    tuta-cowork-bridge gen-api-key  create/rotate the MCP API key in the credential store
    tuta-cowork-bridge totp-code    current 6-digit code from the stored TOTP seed (to confirm
                                    a newly added second factor in Tuta)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from typing import Callable

from . import __version__, vendor

vendor.setup()

from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

from .config import Settings  # noqa: E402
from .credentials import CredentialError, CredentialStore  # noqa: E402
from .http import Guard  # noqa: E402
from .logs import register_secret, setup_logging  # noqa: E402
from .mail import MailReader  # noqa: E402
from .session import TutaClient, TutaSession  # noqa: E402
from .tools import ToolRunner, allowed_tools, build_server, verify_allowlist  # noqa: E402
from .write import AuditLog, MailWriter  # noqa: E402


def build(settings: Settings, store: CredentialStore | None = None,
          client_factory: Callable = TutaClient, audit: AuditLog | None = None) -> tuple:
    store = store or CredentialStore(settings)

    def load_tuta():
        creds = store.tuta()
        register_secret(creds.password)
        register_secret(creds.totp_secret)
        return creds

    tuta = TutaSession(load_tuta, client_factory)
    reader = MailReader(tuta)
    runner = ToolRunner(settings.tool_timeout, settings.max_concurrent_tools)
    writer = None
    if settings.write_groups:
        if not settings.audit_file and audit is None:
            raise CredentialError("TCB_WRITE_TOOLS needs TCB_AUDIT_FILE (undo and daily limits depend on it)")
        writer = MailWriter(tuta, reader, audit or AuditLog(settings.audit_file),
                            daily_write_limit=settings.daily_write_limit,
                            daily_send_limit=settings.daily_send_limit,
                            send_allowlist=settings.send_allowlist)
    mcp = build_server(reader, tuta, runner, settings.source_url, writer, settings.write_groups)
    return mcp, tuta, store


def build_http_app(settings: Settings, store: CredentialStore | None = None,
                   client_factory: Callable = TutaClient, audit: AuditLog | None = None) -> tuple:
    mcp, tuta, store = build(settings, store, client_factory, audit)
    api_key = store.api_key()  # fail fast if missing
    register_secret(api_key)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=list(settings.allowed_origins),
    )
    inner = mcp.streamable_http_app(
        streamable_http_path=settings.mcp_path,
        json_response=True,
        stateless_http=True,
        max_request_body_size=settings.max_body_bytes,
        transport_security=security,
        host=settings.host,
    )
    app = Guard(inner, api_key=api_key, mcp_path=settings.mcp_path, allowed_tools=allowed_tools(settings.write_groups),
                allowed_origins=settings.allowed_origins, max_body=settings.max_body_bytes,
                rate_per_minute=settings.rate_per_minute)
    return app, mcp, tuta


def uvicorn_server(settings: Settings, app):
    import uvicorn

    config = uvicorn.Config(
        app, host=settings.host, port=settings.port, log_config=None, access_log=False,
        proxy_headers=False, server_header=False, date_header=False, lifespan="on",
        limit_concurrency=64, timeout_keep_alive=5, timeout_graceful_shutdown=10,
        h11_max_incomplete_event_size=64 * 1024,
    )
    return uvicorn.Server(config)


async def _serve(settings: Settings) -> None:
    app, mcp, tuta = build_http_app(settings)
    await verify_allowlist(mcp, allowed_tools(settings.write_groups))
    try:
        await uvicorn_server(settings, app).serve()
    finally:
        await tuta.close()


async def _stdio(settings: Settings) -> None:
    mcp, tuta, _ = build(settings)
    await verify_allowlist(mcp, allowed_tools(settings.write_groups))
    try:
        await mcp.run_stdio_async()
    finally:
        await tuta.close()


def _vendor_pins() -> dict:
    pins = {}
    for name in vendor.UPSTREAMS:
        path = vendor.VENDOR_DIR / name
        try:
            rev = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True,
                                 timeout=10).stdout.strip()
            tag = subprocess.run(["git", "-C", str(path), "describe", "--tags", "--exact-match"],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
        except OSError:
            rev, tag = "", ""
        pins[name] = {"commit": rev or "unknown", "tag": tag or None}
    return pins


async def _check(settings: Settings) -> int:
    store = CredentialStore(settings)
    mcp, _, _ = build(settings, store)
    try:
        await verify_allowlist(mcp, allowed_tools(settings.write_groups))
        allowlist_ok = True
    except RuntimeError:
        allowlist_ok = False
    items = store.status()
    report = {
        "version": __version__,
        "listen": f"{settings.host}:{settings.port}{settings.mcp_path}",
        "public_hosts_configured": len(settings.public_hosts),
        "credential_backend": settings.credential_backend,
        "credentials_present": items,
        "tool_allowlist_ok": allowlist_ok,
        "write_groups": list(settings.write_groups),
        "send_allowlist_entries": len(settings.send_allowlist) if "send" in settings.write_groups else 0,
        "tools": sorted(allowed_tools(settings.write_groups)),
        "upstream": _vendor_pins(),
        "telemetry_env": os.environ.get("TUTAPROXY_TELEMETRY"),
    }
    print(json.dumps(report, indent=2))
    ok = allowlist_ok and items["TUTA_EMAIL"] and items["TUTA_PASSWORD"] and items["TCB_API_KEY"]
    return 0 if ok else 1


async def _live_check(settings: Settings) -> int:
    """Minimal live read: counts and booleans only, never names, subjects or bodies."""
    _, tuta, store = build(settings)
    reader = MailReader(tuta)
    try:
        folders = (await reader.list_folders())["folders"]
        page = await reader.list_emails("inbox", limit=3)
        report = {
            "login": "ok",
            "folders": len(folders),
            "folder_types_present": sorted({f["type"] for f in folders}),
            "inbox_page_size": len(page["emails"]),
            "inbox_page_decrypted": sum(1 for e in page["emails"] if e["subject"] != "(cannot decrypt)"),
            "inbox_has_more": page["next_cursor"] is not None,
        }
        print(json.dumps(report, indent=2))
        return 0
    finally:
        await tuta.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tuta-cowork-bridge", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["serve", "stdio", "check", "live-check", "gen-api-key", "totp-code"])
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
    except ValueError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        sys.exit(2)
    setup_logging(settings.log_level, os.environ.get("TCB_LOG_FILE") or None)
    try:
        if args.command == "serve":
            asyncio.run(_serve(settings))
        elif args.command == "stdio":
            asyncio.run(_stdio(settings))
        elif args.command == "check":
            sys.exit(asyncio.run(_check(settings)))
        elif args.command == "live-check":
            sys.exit(asyncio.run(_live_check(settings)))
        elif args.command == "gen-api-key":
            CredentialStore(settings).generate_api_key()
            print(f"API key stored ({settings.credential_backend}: {settings.keychain_service} / TCB_API_KEY). "
                  "Restart the service to use it.")
        elif args.command == "totp-code":
            from tuta.crypto import generate_totp

            seed = CredentialStore(settings).get("TUTA_TOTP_SECRET")
            if not seed:
                raise CredentialError("no TUTA_TOTP_SECRET stored")
            print(f"{generate_totp(seed):06d}  (valid for the current 30 s window)")
    except CredentialError as e:
        print(f"credential error: {e}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
