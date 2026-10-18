import pytest

from tuta_cowork_bridge.mail import InputError, UNTRUSTED_NOTICE, parse_date


async def test_list_folders(reader):
    folders = (await reader.list_folders())["folders"]
    by_type = {f["type"]: f for f in folders}
    assert {"inbox", "sent", "drafts", "custom"} <= set(by_type)
    assert by_type["inbox"]["name"] == "INBOX"
    assert by_type["custom"]["name"] == "Receipts"  # decrypted folder name
    assert set(folders[0]) == {"folder_id", "name", "type"}  # no move/rename handles exposed


async def test_list_emails_newest_first_and_bounded(reader, fake):
    page = await reader.list_emails("inbox", limit=5)
    dates = [e["date"] for e in page["emails"]]
    assert len(dates) == 5 and dates == sorted(dates, reverse=True)
    assert page["emails"][0]["subject"] == "Project kickoff"
    assert page["next_cursor"]
    # only the 5 requested entries and mails were fetched, not the whole folder
    assert [c for c in fake.calls if c[0] == "entries"] == [("entries", "INBOXENTRIES", None, 5)]
    assert [c for c in fake.calls if c[0] == "load"] == [("load", "BAG000000001", 5)]
    assert not any(c[0] == "details" for c in fake.calls)  # no bodies fetched
    assert page["notice"] == UNTRUSTED_NOTICE


async def test_pagination_covers_folder_without_overlap(reader):
    seen, cursor = [], None
    for _ in range(20):
        page = await reader.list_emails("inbox", limit=7, cursor=cursor)
        seen += [e["email_id"] for e in page["emails"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 31  # 30 + the old suspicious one


async def test_unread_only(reader):
    page = await reader.list_emails("inbox", limit=50, unread_only=True)
    assert page["emails"] and all(e["unread"] for e in page["emails"])


async def test_folder_by_name_and_unknown(reader):
    assert (await reader.list_emails("Receipts"))["emails"] == []
    with pytest.raises(InputError):
        await reader.list_emails("nope")


async def test_search_sender_subject_and_correspondent(reader):
    r = await reader.search_emails(sender="alice@example.org")
    assert r["emails"] and all("alice@example.org" in e["from"] for e in r["emails"])
    r = await reader.search_emails(subject="invoice")
    assert r["emails"] and all("Invoice" in e["subject"] for e in r["emails"])
    r = await reader.search_emails(folder="sent", correspondent="ALICE")
    assert [e["subject"] for e in r["emails"]] == ["Re: Project kickoff"]


async def test_search_date_range_stops_early(reader, fake):
    r = await reader.search_emails(since="2026-10-02", until="2026-10-02")
    assert r["emails"] and all(e["date"].startswith("2026-10-02") for e in r["emails"])
    assert r["stopped_because"] == "reached_since_date" and r["next_cursor"] is None


async def test_search_max_scan_and_cursor(reader):
    r = await reader.search_emails(subject="does-not-exist", max_scan=10)
    assert r["scanned"] == 10 and r["stopped_because"] == "max_scan" and r["next_cursor"]
    r2 = await reader.search_emails(subject="does-not-exist", max_scan=1000, cursor=r["next_cursor"])
    assert r2["stopped_because"] == "end_of_folder" and r2["next_cursor"] is None


async def test_search_body_contains_is_bounded(reader):
    r = await reader.search_emails(body_contains="quarterly figures")
    assert r["emails"] and r["bodies_checked"] <= 25
    assert r["stopped_because"] == "body_search_budget" or r["bodies_checked"] <= 25


async def test_read_email(reader, fake):
    first = (await reader.list_emails(limit=1))["emails"][0]
    mail = await reader.read_email(first["email_id"])
    assert mail["subject"] == "Project kickoff"
    assert mail["from"] == "Bob Sample <bob@sample.net>"
    assert mail["to"] == ["Owner <owner@example.test>"]
    assert mail["attachments"] == [{"name": "agenda.pdf", "mime": "application/pdf"}]
    assert "Body 29" in mail["body"] and mail["body_truncated"] is False
    # the mail was fetched once (prefetch reused) and nothing was written
    assert [c for c in fake.calls if c == ("single", first["email_id"].split("/")[1])] == [("single", "M00000000029")]


async def test_reading_does_not_change_unread_state(reader, fake):
    before = fake.unread_flags()
    unread = (await reader.list_emails(limit=50, unread_only=True))["emails"]
    for e in unread[:3]:
        await reader.read_email(e["email_id"])
        await reader.get_email_metadata(e["email_id"])
    assert fake.unread_flags() == before
    assert fake.writes == []


async def test_read_truncation_and_metadata(reader):
    eid = (await reader.search_emails(body_contains="quarterly"))["emails"][0]["email_id"]
    short = await reader.read_email(eid, max_chars=500)
    assert len(short["body"]) <= 500
    meta = await reader.get_email_metadata(eid)
    assert "body" not in meta and meta["subject"]


async def test_thread(reader):
    kickoff = (await reader.search_emails(subject="Project kickoff"))["emails"][0]
    t = await reader.get_thread(kickoff["email_id"], include_bodies=True)
    assert t["thread_size"] == 2
    assert [e["subject"] for e in t["emails"]] == ["Project kickoff", "Re: Project kickoff"]
    assert "see you there" in t["emails"][1]["body"]


async def test_prompt_injection_content_is_returned_as_data(reader, fake):
    r = await reader.search_emails(sender="mallory")
    mail = await reader.read_email(r["emails"][0]["email_id"])
    assert "ignore previous instructions" in mail["subject"]
    assert mail["notice"] == UNTRUSTED_NOTICE
    assert fake.writes == []


@pytest.mark.parametrize("bad", ["", "abc", "a/b/c", "../x/y", "a/b c", "a/" + "x" * 65])
async def test_invalid_email_ids_rejected(reader, bad):
    with pytest.raises(InputError):
        await reader.read_email(bad)


async def test_missing_mail_404(reader):
    from tuta.api import TutaAPIError
    with pytest.raises(TutaAPIError):
        await reader.read_email("BAG000000001/NOPE00000000")


def test_parse_date():
    assert parse_date("2026-10-02") == 1790899200000
    assert parse_date("2026-10-02", end_of_day=True) == 1790899200000 + 86_400_000 - 1
    assert parse_date("2026-10-02T10:00:00+02:00") == 1790899200000 + 8 * 3_600_000
    with pytest.raises(InputError):
        parse_date("yesterday")
