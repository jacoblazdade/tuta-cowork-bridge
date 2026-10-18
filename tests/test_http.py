"""The network boundary: authentication, allowlist, limits, and leak checks."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

import httpx
import pytest

from tuta_cowork_bridge.config import Settings
from tuta_cowork_bridge.main import build_http_app
from tuta_cowork_bridge.tools import ALLOWED_TOOLS

from conftest import API_KEY, FakeStore
from fakes import PASSWORD, FakeTutaClient

HOST = "bridge.example.ts.net"
WRITE_TOOLS = ["tuta_mail_send", "tuta_mail_reply", "tuta_mail_delete", "tuta_mail_move", "tuta_mail_mark",
               "tuta_mail_apply_labels", "tuta_mail_folder_create", "tuta_mail_get_attachment",
               "tuta_calendar_create_event", "tuta_contacts_delete", "tuta_drive_upload", "send_email", "delete_email"]


def rpc(method, params=None, id=1):
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}


@asynccontextmanager
async def running(app):
    """Runs the ASGI lifespan in its own task (anyio cancel scopes must exit in the task that entered them)."""
    started, stop = asyncio.Event(), asyncio.Event()

    async def run():
        async with app.router.lifespan_context(app):
            started.set()
            await stop.wait()

    task = asyncio.create_task(run())
    await started.wait()
    try:
        yield
    finally:
        stop.set()
        await task


@pytest.fixture
async def http(request):
    marker = request.node.get_closest_marker("settings")
    settings = Settings.from_env({"TCB_PUBLIC_HOSTS": HOST, **(marker.args[0] if marker else {})})
    fake = FakeTutaClient()
    app, mcp, tuta = build_http_app(settings, FakeStore(), lambda: fake)
    inner = app.app
    async with running(inner):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"https://{HOST}") as c:
            c.fake = fake
            c.app = app
            yield c


def headers(key=API_KEY, **extra):
    h = {"accept": "application/json, text/event-stream", "content-type": "application/json",
         "mcp-protocol-version": "2025-06-18"}
    if key is not None:
        h["x-api-key"] = key
    h.update(extra)
    return h


async def call(c, method, params=None, key=API_KEY, **extra):
    return await c.post("/mcp", json=rpc(method, params), headers=headers(key, **extra))


async def test_initialize_and_tool_discovery(http):
    r = await call(http, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "t", "version": "1"}})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["serverInfo"]["name"] == "tuta-mail"
    r = await call(http, "tools/list")
    tools = r.json()["result"]["tools"]
    assert {t["name"] for t in tools} == ALLOWED_TOOLS
    assert all(t["annotations"]["readOnlyHint"] is True for t in tools)
    assert all(t["annotations"]["destructiveHint"] is False for t in tools)


@pytest.mark.parametrize("key", [None, "", "wrong", API_KEY[:-1], API_KEY + "x", API_KEY.upper()])
async def test_bad_or_missing_key_rejected(http, key):
    for method in ("initialize", "tools/list", "tools/call"):
        r = await call(http, method, {"name": "list_emails", "arguments": {}}, key=key)
        assert r.status_code == 401
        assert r.json() == {"error": "unauthorized"}
    assert http.fake.logins == 0  # nothing reached Tuta


async def test_unauthenticated_get_and_other_paths(http):
    for path in ("/", "/mcp", "/mcp/", "/healthz", "/docs", "/../etc/passwd"):
        r = await http.get(path)
        assert r.status_code == 401


async def test_authenticated_unknown_path_is_404(http):
    r = await http.get("/admin", headers=headers())
    assert r.status_code == 404


@pytest.mark.parametrize("tool", WRITE_TOOLS)
async def test_write_tools_blocked_at_boundary(http, tool):
    r = await call(http, "tools/call", {"name": tool, "arguments": {"to": ["x@y.z"], "body": "hi"}})
    assert r.status_code == 403
    assert http.fake.writes == [] and http.fake.logins == 0


async def test_write_tool_blocked_inside_batch(http):
    body = [rpc("tools/list", id=1), rpc("tools/call", {"name": "tuta_mail_delete", "arguments": {}}, id=2)]
    r = await http.post("/mcp", json=body, headers=headers())
    assert r.status_code == 403


async def test_write_tool_rejected_by_mcp_layer_too(http):
    """Bypass the HTTP guard entirely: the MCP server itself has no write tools."""
    inner = http.app.app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=inner), base_url=f"https://{HOST}") as raw:
        r = await raw.post("/mcp", json=rpc("tools/call", {"name": "tuta_mail_send", "arguments": {}}),
                           headers=headers(key=None))
    assert r.status_code == 200
    body = r.json()
    assert "error" in body or body["result"].get("isError") is True
    assert http.fake.writes == [] and http.fake.logins == 0


async def test_allowed_tool_call_end_to_end(http):
    r = await call(http, "tools/call", {"name": "list_emails", "arguments": {"limit": 2}})
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    data = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    assert len(data["emails"]) == 2
    r = await call(http, "tools/call", {"name": "read_email", "arguments": {"email_id": data["emails"][0]["email_id"]}})
    assert "Project kickoff" in r.text
    assert http.fake.writes == []


async def test_tool_errors_are_generic(http):
    r = await call(http, "tools/call", {"name": "read_email", "arguments": {"email_id": "BAG000000001/MISSING00000"}})
    assert r.status_code == 200
    assert "Not found in the mailbox" in r.text and "TutaAPI" not in r.text


async def test_secrets_never_in_responses(http):
    texts = []
    for name, args in [("connector_status", {}), ("list_folders", {}), ("list_emails", {"limit": 50}),
                       ("search_emails", {"sender": "alice"})]:
        texts.append((await call(http, "tools/call", {"name": name, "arguments": args})).text)
    texts.append((await call(http, "tools/list")).text)
    blob = "\n".join(texts)
    for secret in (API_KEY, PASSWORD, "tok1"):
        assert secret not in blob


async def test_foreign_origin_rejected(http):
    r = await call(http, "tools/list", origin="https://evil.example")
    assert r.status_code == 403
    r = await call(http, "tools/list", origin="https://claude.ai")
    assert r.status_code == 200


async def test_unknown_host_rejected(http):
    r = await http.post("https://attacker.example/mcp", json=rpc("tools/list"), headers=headers())
    assert r.status_code in (403, 421)


@pytest.mark.settings({"TCB_MAX_BODY_BYTES": "2048"})
async def test_body_size_limit(http):
    big = rpc("tools/call", {"name": "search_emails", "arguments": {"subject": "x" * 5000}})
    r = await http.post("/mcp", content=json.dumps(big), headers=headers())
    assert r.status_code == 413


@pytest.mark.settings({"TCB_RATE_PER_MINUTE": "60"})
async def test_rate_limit(http):
    codes = [(await call(http, "tools/list")).status_code for _ in range(25)]
    assert 429 in codes and codes[0] == 200


async def test_auth_failure_lockout_does_not_block_valid_key(http):
    codes = [(await call(http, "tools/list", key="bad")).status_code for _ in range(25)]
    assert codes[0] == 401 and codes[-1] == 429
    assert (await call(http, "tools/list")).status_code == 200


async def test_logs_contain_no_secrets_or_content(http, caplog):
    caplog.set_level(logging.DEBUG)
    await call(http, "tools/call", {"name": "list_emails", "arguments": {"limit": 5}})
    await call(http, "tools/call", {"name": "read_email", "arguments": {"email_id": "BAG000000001/M00000000029"}})
    await call(http, "tools/list", key="guess-" + API_KEY[:10])
    from tuta_cowork_bridge.logs import RedactingFormatter
    fmt = RedactingFormatter("%(message)s")
    out = "\n".join(fmt.format(r) for r in caplog.records)
    for leak in (API_KEY, PASSWORD, "Project kickoff", "Body 29", "alice@example.org", "owner@example.test"):
        assert leak not in out
