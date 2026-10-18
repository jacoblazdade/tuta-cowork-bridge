"""Runs the real uvicorn server on a loopback socket with the fake Tuta client."""

import asyncio
import socket

import httpx

from tuta_cowork_bridge.config import Settings
from tuta_cowork_bridge.main import build_http_app, uvicorn_server

from conftest import API_KEY, FakeStore
from fakes import FakeTutaClient


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_real_server_binds_loopback_only():
    port = _free_port()
    settings = Settings.from_env({"TCB_PORT": str(port)})
    app, _, tuta = build_http_app(settings, FakeStore(), lambda: FakeTutaClient())
    server = uvicorn_server(settings, app)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    try:
        sockets = [s.getsockname() for srv in server.servers for s in srv.sockets]
        assert sockets and all(addr[0] == "127.0.0.1" for addr in sockets)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
            r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            assert r.status_code == 401 and "server" not in r.headers
            r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                             headers={"x-api-key": API_KEY, "accept": "application/json, text/event-stream",
                                      "mcp-protocol-version": "2025-06-18"})
            assert r.status_code == 200 and len(r.json()["result"]["tools"]) == 7
    finally:
        server.should_exit = True
        await task
        await tuta.close()
