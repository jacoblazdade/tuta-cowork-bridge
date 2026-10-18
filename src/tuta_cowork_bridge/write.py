"""Opt-in mailbox modifications (TCB_WRITE_TOOLS), with server-side guardrails.

Groups:
    organize  move_emails, trash_emails, restore_emails, mark_emails
    folders   create_folder, rename_folder, label_emails
    send      send_email

Guardrails that hold no matter what the client asks:
- at most MAX_BATCH emails per call and TCB_DAILY_WRITE_LIMIT affected emails per day;
- "delete" means Trash: permanent deletion and folder deletion are not implemented;
- Trash records each email's original folder, so restore_emails can undo it;
- sending: every recipient must match TCB_SEND_ALLOWLIST (default: only your own
  address), no BCC, no attachments, TCB_DAILY_SEND_LIMIT per day, and no automatic
  retry (a retry after a lost response could send twice);
- an audit log (JSON lines) records action, ids, folder ids, counts and recipient
  domains: never subjects, bodies or full addresses.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from . import vendor

vendor.setup()
from tutamcp.tools_mail import _parse_address, _send_core  # noqa: E402

from .mail import Folder, InputError, MailReader, _load_mails, _ref, parse_email_id  # noqa: E402
from .session import TutaSession  # noqa: E402

log = logging.getLogger(__name__)

MAX_BATCH = 50
MAX_RECIPIENTS = 10
MAX_SUBJECT = 300
MAX_SEND_BODY = 50_000
MAX_FOLDER_NAME = 100
ADDRESS_RE = re.compile(r"^[^@\s<>\"',;]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
MOVE_TARGET_TYPES = {"inbox", "archive", "spam", "custom"}  # trash via trash_emails; not into sent/drafts
SYSTEM_TYPE_CODES = {"inbox": "1", "sent": "2", "trash": "3", "archive": "4", "spam": "5", "drafts": "6"}


class AuditLog:
    """Append-only JSON-lines audit trail; also the source for daily caps and undo."""

    def __init__(self, path: str | None, clock=time.time):
        self.path = path
        self.clock = clock
        self._mem: list[dict] = []
        if path:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def _entries(self) -> list[dict]:
        if not self.path:
            return self._mem
        try:
            with open(self.path, encoding="utf-8") as f:
                return [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []

    def record(self, action: str, **fields: Any) -> None:
        entry = {"ts": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(), "action": action, **fields}
        if not self.path:
            self._mem.append(entry)
            return
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def used_today(self, actions: set[str]) -> int:
        today = datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat()
        return sum(e.get("count", 0) for e in self._entries()
                   if e.get("action") in actions and e.get("ts", "").startswith(today))

    def trash_origin(self, email_id: str) -> str | None:
        """Folder id the email was in before its most recent trash_emails."""
        for e in reversed(self._entries()):
            if e.get("action") == "trash" and email_id in e.get("origins", {}):
                return e["origins"][email_id]
        return None


WRITE_ACTIONS = {"move", "trash", "restore", "mark", "label"}


class MailWriter:
    def __init__(self, tuta: TutaSession, reader: MailReader, audit: AuditLog, *,
                 daily_write_limit: int, daily_send_limit: int, send_allowlist: tuple[str, ...]):
        self._tuta, self._reader, self._audit = tuta, reader, audit
        self._daily_write = daily_write_limit
        self._daily_send = daily_send_limit
        self._allow = send_allowlist

    # ---- organize ----

    async def move_emails(self, email_ids: list[str], destination: str) -> dict:
        refs = self._refs(email_ids)

        async def run(client, session, mgk):
            target = self._reader._resolve(await self._reader._folders(client, session, mgk), destination)
            if target.type not in MOVE_TARGET_TYPES:
                raise InputError(f"cannot move into {target.type!r}; allowed: inbox, archive, spam or a custom "
                                 "folder (use trash_emails to delete, label_emails for labels)")
            found, missing = await self._existing(client, session, refs)
            self._charge(len(found))
            await self._move(client, session, found, target)
            self._audit.record("move", count=len(found), ids=[_eid(r) for r in found], to=target.folder_id)
            return {"moved": len(found), "destination": target.name, "not_found": missing}

        return await self._tuta.run(run)

    async def trash_emails(self, email_ids: list[str]) -> dict:
        refs = self._refs(email_ids)

        async def run(client, session, mgk):
            folders = await self._reader._folders(client, session, mgk)
            by_set = {f.set_id: f for f in folders}
            trash = next(f for f in folders if f.type == "trash")
            mails = await _load_mails(client, session, refs)
            found = [r for r in refs if r in mails]
            origins = {}
            for r in found:
                origin = self._current_folder(mails[r], by_set)
                if origin is not None and origin.folder_id != trash.folder_id:
                    origins[_eid(r)] = origin.folder_id
            self._charge(len(found))
            if found:
                await client.simple_move_mails(session, found, SYSTEM_TYPE_CODES["trash"])
            self._audit.record("trash", count=len(found), ids=[_eid(r) for r in found], origins=origins)
            return {"moved_to_trash": len(found), "not_found": [_eid(r) for r in refs if r not in mails],
                    "note": "Recoverable with restore_emails or from Trash in Tuta."}

        return await self._tuta.run(run)

    async def restore_emails(self, email_ids: list[str]) -> dict:
        refs = self._refs(email_ids)

        async def run(client, session, mgk):
            folders = await self._reader._folders(client, session, mgk)
            by_id = {f.folder_id: f for f in folders}
            by_set = {f.set_id: f for f in folders}
            inbox = next(f for f in folders if f.type == "inbox")
            mails = await _load_mails(client, session, refs)
            groups: dict[str, list[tuple[str, str]]] = {}
            skipped = []
            for r in refs:
                current = self._current_folder(mails[r], by_set) if r in mails else None
                if current is None or current.type != "trash":
                    skipped.append(_eid(r))
                    continue
                origin = by_id.get(self._audit.trash_origin(_eid(r)) or "", inbox)
                groups.setdefault(origin.folder_id, []).append(r)
            restored = sum(len(v) for v in groups.values())
            self._charge(restored)
            for folder_id, group in groups.items():
                await self._move(client, session, group, by_id[folder_id])
            self._audit.record("restore", count=restored, ids=[_eid(r) for g in groups.values() for r in g])
            return {"restored": restored,
                    "destinations": {by_id[k].name: len(v) for k, v in groups.items()},
                    "skipped_not_in_trash": skipped}

        return await self._tuta.run(run)

    async def mark_emails(self, email_ids: list[str], unread: bool) -> dict:
        refs = self._refs(email_ids)

        async def run(client, session, mgk):
            found, missing = await self._existing(client, session, refs)
            self._charge(len(found))
            if found:
                await client.mark_mails_unread(session, found, bool(unread))
            self._audit.record("mark", count=len(found), ids=[_eid(r) for r in found], unread=bool(unread))
            return {"marked": len(found), "unread": bool(unread), "not_found": missing}

        return await self._tuta.run(run)

    # ---- folders & labels ----

    async def create_folder(self, name: str, parent: str | None = None) -> dict:
        name = _folder_name(name)

        async def run(client, session, mgk):
            folders = await self._reader._folders(client, session, mgk)
            parent_f = None
            if parent:
                parent_f = self._reader._resolve(folders, parent)
                if parent_f.type != "custom":
                    raise InputError("parent must be a custom folder")
            siblings = [f for f in folders if f.type == "custom" and f.name.lower() == name.lower()]
            if siblings:
                raise InputError(f"a folder named {name!r} already exists")
            await client.create_folder(session, name, mgk, parent_f.raw if parent_f else None)
            self._reader.invalidate_folders()
            created = self._reader._resolve(await self._reader._folders(client, session, mgk), name)
            self._audit.record("create_folder", count=1, folder=created.folder_id)
            return {"created": created.public()}

        return await self._tuta.run(run)

    async def rename_folder(self, folder: str, new_name: str) -> dict:
        new_name = _folder_name(new_name)

        async def run(client, session, mgk):
            target = self._reader._resolve(await self._reader._folders(client, session, mgk), folder)
            if target.type != "custom":
                raise InputError("only custom folders can be renamed")
            await client.rename_folder(session, target.raw, new_name, mgk)
            self._reader.invalidate_folders()
            self._audit.record("rename_folder", count=1, folder=target.folder_id)
            return {"renamed": target.folder_id, "new_name": new_name}

        return await self._tuta.run(run)

    async def label_emails(self, email_ids: list[str], add_labels: list[str] | None = None,
                           remove_labels: list[str] | None = None) -> dict:
        refs = self._refs(email_ids)
        if not add_labels and not remove_labels:
            raise InputError("pass add_labels and/or remove_labels")

        async def run(client, session, mgk):
            folders = await self._reader._folders(client, session, mgk)

            def labels(names):
                out = []
                for n in names or []:
                    f = self._reader._resolve(folders, n)
                    if f.type != "label":
                        raise InputError(f"{n!r} is a folder, not a label (use move_emails)")
                    out.append(f.set_id)
                return out

            add, remove = labels(add_labels), labels(remove_labels)
            found, missing = await self._existing(client, session, refs)
            self._charge(len(found))
            if found:
                await client.apply_labels(session, found, add, remove)
            self._audit.record("label", count=len(found), ids=[_eid(r) for r in found], added=len(add),
                               removed=len(remove))
            return {"labelled": len(found), "added": len(add), "removed": len(remove), "not_found": missing}

        return await self._tuta.run(run)

    # ---- send ----

    async def send_email(self, to: list[str], subject: str, body: str, cc: list[str] | None = None) -> dict:
        to_list = [_recipient(a) for a in to or []]
        cc_list = [_recipient(a) for a in cc or []]
        if not to_list:
            raise InputError("at least one recipient in 'to' is required")
        if len(to_list) + len(cc_list) > MAX_RECIPIENTS:
            raise InputError(f"at most {MAX_RECIPIENTS} recipients")
        subject = (subject or "").strip()
        if not subject or len(subject) > MAX_SUBJECT or any(c in subject for c in "\r\n"):
            raise InputError(f"subject must be one line of 1-{MAX_SUBJECT} characters")
        if not body or len(body) > MAX_SEND_BODY:
            raise InputError(f"body must be 1-{MAX_SEND_BODY} characters")
        used = self._audit.used_today({"send"})
        if used >= self._daily_send:
            raise InputError(f"daily send limit reached ({self._daily_send}); try again tomorrow")

        async def run(client, session, mgk):
            own = (session.user_email or "").lower()
            blocked = [a for _, a in to_list + cc_list if not self._allowed(a, own)]
            if blocked:
                raise InputError(f"recipient(s) not on the send allowlist: {', '.join(blocked)}. "
                                 "The owner can extend TCB_SEND_ALLOWLIST.")
            result = await _send_core(
                client, session, mgk, subject=subject, body_text=html.escape(body),
                from_addr=session.user_email, from_name="", to_list=to_list, cc_list=cc_list, bcc_list=[],
                attachment_paths=None,
            )
            domains = sorted({a.split("@")[1] for _, a in to_list + cc_list})
            self._audit.record("send", count=1, recipients=len(to_list) + len(cc_list), domains=domains,
                               e2e=bool(result.get("e2e")))
            return {"status": "sent", "to": result.get("to"), "cc": result.get("cc"), "subject": subject,
                    "end_to_end_encrypted": bool(result.get("e2e"))}

        # no automatic retry on network errors: the first attempt may already have sent
        return await self._tuta.run(run, retry_transient=False)

    # ---- helpers ----

    def _allowed(self, address: str, own: str) -> bool:
        address = address.lower()
        for entry in self._allow:
            if entry == "*" or (entry == "me" and address == own) or address == entry:
                return True
            if entry.startswith("@") and address.endswith(entry):
                return True
        return False

    def _refs(self, email_ids: list[str]) -> list[tuple[str, str]]:
        if not email_ids:
            raise InputError("email_ids is empty")
        refs = list(dict.fromkeys(parse_email_id(e) for e in email_ids))
        if len(refs) > MAX_BATCH:
            raise InputError(f"at most {MAX_BATCH} emails per call; split the request")
        return refs

    def _charge(self, n: int) -> None:
        used = self._audit.used_today(WRITE_ACTIONS)
        if used + n > self._daily_write:
            raise InputError(f"daily limit of {self._daily_write} modified emails would be exceeded "
                             f"({used} used today)")

    async def _existing(self, client, session, refs):
        mails = await _load_mails(client, session, refs)
        return [r for r in refs if r in mails], [_eid(r) for r in refs if r not in mails]

    @staticmethod
    def _current_folder(raw: dict, by_set: dict[tuple[str, str], Folder]) -> Folder | None:
        """The (non-label) folder a mail is in, from Mail.sets (1465)."""
        for s in raw.get("1465") or []:
            r = _ref([s]) if isinstance(s, list) and s and isinstance(s[0], str) else _ref(s)
            f = by_set.get(r) if r else None
            if f is not None and f.type != "label":
                return f
        return None

    @staticmethod
    async def _move(client, session, refs, target: Folder) -> None:
        if not refs:
            return
        if target.type in ("inbox", "archive", "spam", "trash"):
            await client.simple_move_mails(session, refs, SYSTEM_TYPE_CODES[target.type])
        else:
            await client.move_mails_to_folder(session, refs, target.set_id[0], target.set_id[1])


def _eid(ref: tuple[str, str]) -> str:
    return f"{ref[0]}/{ref[1]}"


def _folder_name(name: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > MAX_FOLDER_NAME or any(ord(c) < 32 for c in name):
        raise InputError(f"folder name must be 1-{MAX_FOLDER_NAME} printable characters")
    return name


def _recipient(value: str) -> tuple[str, str]:
    name, addr = _parse_address(value or "")
    if not ADDRESS_RE.match(addr):
        raise InputError(f"invalid email address: {value!r}")
    return name, addr.lower()
