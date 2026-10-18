# Security

## Reporting

Please report vulnerabilities privately through GitHub's *Report a vulnerability*
(Security advisories) on this repository, not in public issues.

## What is protected, and from whom

The bridge holds the keys to a real, end-to-end encrypted mailbox. It is designed so that:

| Threat | Control |
|---|---|
| Anyone on the internet reaching the public URL | Every request needs a 256-bit `X-API-Key` (constant-time comparison) before any MCP or Tuta code runs. Repeated failures trigger a 5-minute lockout for unauthenticated requests without affecting the valid key. |
| A compromised or confused AI client | Default: only 7 read-only tools exist. Write groups are opt-in per deployment (`TCB_WRITE_TOOLS`). `tools/call` names are checked against the configured allowlist in the HTTP guard *and* the MCP registry is verified against the same allowlist at startup. Permanent deletion, folder deletion, attachments, filesystem and shell are never reachable. |
| Prompt injection in email | Mail is returned as data with an explicit untrusted-content notice; server instructions tell the model never to follow instructions in mail and to confirm with the owner before trashing, moving or sending. Read-only deployment: a hijacked model can only read. With write tools: see below. |
| LAN / other local apps | Listener is loopback-only (`127.0.0.1`); non-loopback binds are refused unless explicitly enabled for an isolated container. The bridge only proxies to Tuta; it cannot reach other local services. |
| DNS rebinding / browser CSRF | Host-header allowlist and Origin allowlist (MCP SDK transport security + the guard). |
| Resource abuse | Body limit (256 KiB), rate limit for authenticated traffic, per-tool timeout, concurrency cap, bounded scans, uvicorn connection limits. |
| Credential leakage | Secrets in the macOS Keychain; never in argv, env files, the repo, logs or tool output. Logs are redacted (tokens, API key, Tuta password/TOTP seed, email addresses) on the final formatted line including tracebacks, and record only method/path/status/timing. |
| Account lockout / login storms | Failed logins back off exponentially (wrong credentials: 5 min doubling to 1 h). |

### With write tools enabled

Enabling write tools turns prompt injection from "misleads Claude" into "can change the
mailbox", because the server cannot tell an owner's request from a tricked model — both arrive
as the same tool call. Mitigations, from strongest to weakest:

- **Sending is limited by a server-side recipient allowlist** (default: only the owner's own
  address). This is what stops a malicious email from exfiltrating mail to an attacker. `*`
  disables it; don't use `*` unless you review every send.
- **Damage is bounded and reversible:** no permanent or folder deletion; Trash instead of delete
  with `restore_emails`; ≤ 50 emails per call; daily caps on modified emails and sends.
- **Audit trail:** every write is recorded (ids, folder ids, counts, recipient domains) in a
  `0600` JSON-lines file outside the repository.
- **Client-side confirmation:** write tools are annotated non-read-only / destructive so MCP
  clients can require approval; keep approval prompts on for `trash_emails` and `send_email`.
- **No duplicate sends:** sending is never retried automatically after a network error.

**Not protected against:** an attacker who controls your unlocked macOS user session. Such an
attacker can read the Keychain items the same way the bridge does. Storing the Tuta password
(and a TOTP seed) for unattended login means two-factor authentication no longer protects the
account against compromise of this machine. Use a dedicated TOTP factor so it can be revoked
independently, keep the machine patched, and use FileVault.

## Credentials

| Item | Where | Notes |
|---|---|---|
| `TUTA_EMAIL`, `TUTA_PASSWORD` | Keychain, service `tuta-cowork-bridge` | Tuta has no app passwords; the password also derives the mailbox keys. |
| `TUTA_TOTP_SECRET` (optional) | Keychain | Seed of a bridge-only TOTP factor. Present in the process environment only for the duration of a login call (tutaproxy reads it from there), then removed. Never your recovery code. |
| `TCB_API_KEY` | Keychain | 256-bit random (`secrets.token_urlsafe(32)`); written through `security -i` on stdin, never argv. |

Items are created by `/usr/bin/security`, which therefore is the only app on their ACL; the
bridge reads them through the same binary, so no other application gains access and no GUI
prompt appears. The file backend (non-macOS) refuses files that are group/world accessible or
owned by another user.

To revoke everything: remove the bridge's TOTP factor in Tuta, change the Tuta password if
compromise is suspected, rotate the API key (`scripts/setup.sh --rotate-key`), and close active
sessions in Tuta's settings.

## Upstream review (tutaproxy v1.3.18, TutaMCP v0.1.8)

Reviewed before connecting a real account. Summary:

- **Telemetry:** `tuta/telemetry.py` POSTs an install UUID + version to a raw IP
  (`176.121.81.52:442`, pinned private CA) and checks GitHub releases. It is started only by
  tutaproxy's `run_proxy.py` (the IMAP/SMTP proxy). The bridge never imports it — a test asserts
  this — and still pins `TUTAPROXY_TELEMETRY=false`, which makes `startup()` return before any
  network I/O.
- **Network:** `TutaClient` talks only to `https://app.tuta.com` and Tuta blob servers
  (`w*.api.tuta.com`), with default certificate verification (certifi bundle). No TLS
  verification is disabled.
- **Integrity:** AES-CBC + HMAC-SHA256 with constant-time MAC comparison. The upstream kill switch
  `TUTA_SKIP_HMAC=1` would disable it; the bridge refuses to start if it is set.
- **Login:** Argon2id/bcrypt verifier computed locally; the password is never sent. TOTP codes
  are generated locally (RFC 6238). The access token appears in blob-fetch URL query parameters
  (Tuta's own protocol) — the bridge's log redaction covers it.
- **Logging:** upstream logs the login email at INFO and API payload samples at DEBUG; the bridge
  sets upstream loggers to WARNING and redacts all output.
- **Persistence:** `TutaClient` stores nothing on disk. tutaproxy's on-disk cache is used only by
  its IMAP server, which the bridge does not use. No decrypted mail is persisted.
- **No dangerous primitives** (`subprocess`, `eval`, `pickle`, `os.system`) in the code paths used.
- **Write paths used (opt-in):** `simplemovemailservice`, `movemailservice`,
  `unreadmailstateservice`, `applylabelservice`, `mailfolderservice` (create), `mailset` PUT
  (rename), and for sending `draftservice`, `senddraftservice`, `publickeyservice` — all Tuta's own
  services, no other hosts, no hidden recipients. Upstream's send helper does not HTML-escape the
  body; the bridge escapes it before calling it. Verified live on a self-sent test message.
- **TutaMCP tools are not used:** `tuta_mail_reply`, `move`, `delete`, `mark` and `apply_labels`
  are registered unconditionally (so "reply_only" is *not* read-only), and
  `tuta_mail_get_attachment` writes files to disk. The bridge imports only TutaMCP's pure
  decryption helpers and registers its own tools.
- **Performance issue (not security):** TutaMCP's list call downloads every mail of every mail bag
  a folder references on each call; the bridge replaces it with a bounded newest-first reader.
- Dependencies are pinned in `uv.lock`; upstream code is pinned by submodule commit. Upgrading
  either never adds tools — the allowlist is part of this repository.

## Tests that guard these properties

`tests/test_http.py` (auth, allowlist, limits, origin/host, leak checks),
`tests/test_config_security.py` (loopback enforcement, telemetry, HMAC switch, forbidden
upstream calls, file-backend permissions, redaction), `tests/test_session.py` (backoff, re-login,
TOTP handling), `tests/test_mail.py` (no writes, unread state unchanged, bounded fetching).
`scripts/external-check.sh` repeats the boundary checks against the public URL.
