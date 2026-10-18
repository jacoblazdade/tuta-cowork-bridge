"""Opt-in write tools: behaviour and guardrails."""

import json

import aiohttp
import pytest

from tuta_cowork_bridge.config import Settings
from tuta_cowork_bridge.mail import InputError
from tuta_cowork_bridge.tools import READ_TOOLS, ToolRunner, allowed_tools, build_server, verify_allowlist
from tuta_cowork_bridge.write import AuditLog, MailWriter

from fakes import EMAIL


def writer_for(tuta, reader, audit=None, **kw):
    opts = dict(daily_write_limit=500, daily_send_limit=20, send_allowlist=("me",))
    opts.update(kw)
    return MailWriter(tuta, reader, audit or AuditLog(None), **opts)


async def inbox_ids(reader, n=3):
    return [e["email_id"] for e in (await reader.list_emails("inbox", limit=n))["emails"]]


async def test_trash_and_restore_roundtrip(reader, tuta, fake):
    w = writer_for(tuta, reader)
    ids = await inbox_ids(reader)
    r = await w.trash_emails(ids)
    assert r["moved_to_trash"] == 3
    assert ("simple_move", "3", tuple(tuple(i.split("/")) for i in ids)) in fake.modifications
    trash = {e["email_id"] for e in (await reader.list_emails("trash", limit=50))["emails"]}
    assert set(ids) <= trash
    r = await w.restore_emails(ids)
    assert r["restored"] == 3 and r["destinations"] == {"INBOX": 3}
    assert set(ids) <= {e["email_id"] for e in (await reader.list_emails("inbox", limit=50))["emails"]}
    assert fake.writes == []  # no permanent delete or other forbidden call


async def test_restore_returns_to_custom_origin(reader, tuta, fake):
    w = writer_for(tuta, reader)
    ids = await inbox_ids(reader, 2)
    await w.move_emails(ids, "Receipts")
    await w.trash_emails(ids)
    r = await w.restore_emails(ids)
    assert r["destinations"] == {"Receipts": 2}


async def test_restore_skips_emails_not_in_trash(reader, tuta):
    w = writer_for(tuta, reader)
    r = await w.restore_emails(await inbox_ids(reader, 1))
    assert r["restored"] == 0 and len(r["skipped_not_in_trash"]) == 1


async def test_move_targets(reader, tuta, fake):
    w = writer_for(tuta, reader)
    ids = await inbox_ids(reader, 2)
    assert (await w.move_emails(ids, "archive"))["destination"] == "Archive"
    assert (await w.move_emails(ids, "Receipts"))["moved"] == 2
    assert fake.modifications[-1][0] == "move"
    for bad in ("trash", "sent", "drafts", "Important"):
        with pytest.raises(InputError):
            await w.move_emails(ids, bad)


async def test_mark_and_label(reader, tuta, fake):
    w = writer_for(tuta, reader)
    ids = await inbox_ids(reader, 2)
    await w.mark_emails(ids, unread=True)
    assert all(e["unread"] for e in (await reader.list_emails("inbox", limit=2))["emails"])
    r = await w.label_emails(ids, add_labels=["Important"])
    assert r["labelled"] == 2 and fake.modifications[-1][0] == "label"
    with pytest.raises(InputError):
        await w.label_emails(ids, add_labels=["Receipts"])  # a folder, not a label
    with pytest.raises(InputError):
        await w.label_emails(ids)


async def test_create_and_rename_folder(reader, tuta, fake):
    w = writer_for(tuta, reader)
    r = await w.create_folder("Freelance 2026")
    assert r["created"]["name"] == "Freelance 2026" and r["created"]["type"] == "custom"
    assert (await w.create_folder("Sub", parent="Freelance 2026"))["created"]["name"] == "Sub"
    with pytest.raises(InputError):
        await w.create_folder("freelance 2026")  # duplicate
    with pytest.raises(InputError):
        await w.create_folder("bad\nname")
    assert (await w.rename_folder("Freelance 2026", "Selbständigkeit"))["new_name"] == "Selbständigkeit"
    names = {f["name"] for f in (await reader.list_folders())["folders"]}
    assert "Selbständigkeit" in names
    with pytest.raises(InputError):
        await w.rename_folder("inbox", "x")


async def test_batch_and_daily_limits(reader, tuta):
    w = writer_for(tuta, reader, daily_write_limit=4)
    with pytest.raises(InputError):
        await w.trash_emails([f"BAG000000001/M{i:011d}" for i in range(51)])
    with pytest.raises(InputError):
        await w.trash_emails([])
    ids = await inbox_ids(reader, 5)
    await w.mark_emails(ids[:3], unread=False)
    with pytest.raises(InputError) as e:
        await w.mark_emails(ids[3:5], unread=False)
    assert "daily limit" in str(e.value)


async def test_audit_log_is_private_and_contentless(reader, tuta, tmp_path):
    path = tmp_path / "audit.jsonl"
    w = writer_for(tuta, reader, AuditLog(str(path)), send_allowlist=("me",))
    ids = await inbox_ids(reader, 2)
    await w.trash_emails(ids)
    await w.send_email([EMAIL], "Hello", "secret body text")
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    text = path.read_text()
    assert "secret body text" not in text and "Hello" not in text and EMAIL not in text
    entries = [json.loads(line) for line in text.splitlines()]
    assert [e["action"] for e in entries] == ["trash", "send"]
    assert entries[1]["domains"] == ["example.test"]
    # undo survives a restart: a new writer reads origins from the same file
    w2 = writer_for(tuta, reader, AuditLog(str(path)))
    assert (await w2.restore_emails(ids))["restored"] == 2


async def test_send_allowlist(reader, tuta, fake):
    w = writer_for(tuta, reader, send_allowlist=("me", "@example.org"))
    r = await w.send_email(["Alice <alice@example.org>"], "Re: Project kickoff", "Thanks!\n<b>x</b>", cc=[EMAIL])
    assert r["status"] == "sent" and fake.sent[-1]["bcc"] == []
    assert "&lt;b&gt;" in fake.sent[-1]["html"] and "<b>" not in fake.sent[-1]["html"]  # body is escaped
    for bad in (["mallory@evil.test"], ["alice@example.org.evil.test"], ["alice@sub.example.org"]):
        with pytest.raises(InputError) as e:
            await w.send_email(bad, "Hi", "body")
        assert "allowlist" in str(e.value)
    with pytest.raises(InputError):
        await w.send_email(["alice@example.org"], "Hi", "body", cc=["mallory@evil.test"])
    assert len(fake.sent) == 1


async def test_send_validation_and_daily_cap(reader, tuta, fake):
    w = writer_for(tuta, reader, daily_send_limit=1)
    for to, subject, body in [([], "s", "b"), (["not-an-address"], "s", "b"), ([EMAIL], "", "b"),
                              ([EMAIL], "a\nBcc: x@y.z", "b"), ([EMAIL], "s", ""),
                              ([f"u{i}@example.test" for i in range(11)], "s", "b")]:
        with pytest.raises(InputError):
            await w.send_email(to, subject, body)
    await w.send_email([EMAIL], "one", "body")
    with pytest.raises(InputError) as e:
        await w.send_email([EMAIL], "two", "body")
    assert "daily send limit" in str(e.value)


async def test_send_is_not_retried_on_network_error(reader, tuta, fake):
    w = writer_for(tuta, reader)
    await reader.list_folders()  # log in first
    fake.fail_next = [aiohttp.ClientConnectionError("lost response")]
    with pytest.raises(aiohttp.ClientConnectionError):
        await w.send_email([EMAIL], "once", "body")
    assert [m for m in fake.modifications if m[0] == "send"] == []
    assert len(fake.sent) == 1  # one draft attempt, no second try


async def test_tool_registration_per_group(reader, tuta):
    w = writer_for(tuta, reader)
    for groups in [(), ("organize",), ("folders",), ("send",), ("organize", "folders", "send")]:
        mcp = build_server(reader, tuta, ToolRunner(10, 2), "https://example.invalid", w if groups else None, groups)
        await verify_allowlist(mcp, allowed_tools(groups))
        tools = {t.name: t for t in await mcp.list_tools()}
        for name in set(tools) - READ_TOOLS:
            assert tools[name].annotations.read_only_hint is False
        for name in READ_TOOLS:
            assert tools[name].annotations.read_only_hint is True
        if "send" in groups:
            assert tools["send_email"].annotations.destructive_hint is True
            assert tools["send_email"].annotations.idempotent_hint is False
        if "organize" in groups:
            assert tools["trash_emails"].annotations.destructive_hint is True
    with pytest.raises(ValueError):
        build_server(reader, tuta, ToolRunner(10, 2), "x", None, ("organize",))


def test_settings_validation():
    s = Settings.from_env({"TCB_WRITE_TOOLS": "organize, send", "TCB_SEND_ALLOWLIST": "me,@Example.org,a@b.de"})
    assert s.write_groups == ("organize", "send") and s.send_allowlist == ("me", "@example.org", "a@b.de")
    assert Settings.from_env({}).send_allowlist == ("me",)
    with pytest.raises(ValueError):
        Settings.from_env({"TCB_WRITE_TOOLS": "delete"})
    with pytest.raises(ValueError):
        Settings.from_env({"TCB_SEND_ALLOWLIST": "everyone"})


async def test_http_guard_respects_enabled_groups():
    import httpx
    from tuta_cowork_bridge.main import build_http_app
    from conftest import API_KEY, FakeStore
    from fakes import FakeTutaClient
    from test_http import HOST, headers, rpc, running

    async def status(groups, tool, args):
        env = {"TCB_PUBLIC_HOSTS": HOST, "TCB_WRITE_TOOLS": ",".join(groups)}
        app, _, _ = build_http_app(Settings.from_env(env), FakeStore(), lambda: FakeTutaClient(), AuditLog(None))
        async with running(app.app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"https://{HOST}") as c:
                r = await c.post("/mcp", json=rpc("tools/call", {"name": tool, "arguments": args}), headers=headers())
                return r.status_code, r.text

    ids = {"email_ids": ["BAG000000001/M00000000001"]}
    code, text = await status(("organize",), "mark_emails", {**ids, "unread": True})
    result = json.loads(text)["result"]
    assert code == 200 and result["isError"] is False
    assert json.loads(result["content"][0]["text"])["marked"] == 1
    assert (await status(("organize",), "send_email", {"to": [EMAIL], "subject": "s", "body": "b"}))[0] == 403
    assert (await status(("organize", "folders", "send"), "tuta_mail_delete", ids))[0] == 403
    for tool in ("move_emails", "trash_emails", "send_email", "create_folder"):
        assert (await status((), tool, ids))[0] == 403  # read-only default
