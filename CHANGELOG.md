# Changelog

All notable changes to this project. Versions follow [Semantic Versioning](https://semver.org/).

## 1.1.0 — 2026-10-18

### Added
- Opt-in write groups via `TCB_WRITE_TOOLS` (read-only remains the default):
  - `organize`: `move_emails`, `trash_emails`, `restore_emails`, `mark_emails`
  - `folders`: `create_folder`, `rename_folder`, `label_emails`
  - `send`: `send_email`, restricted by `TCB_SEND_ALLOWLIST` (default: own address only)
- Guardrails: at most 50 emails per call, daily caps (`TCB_DAILY_WRITE_LIMIT`,
  `TCB_DAILY_SEND_LIMIT`), Trash instead of deletion with restore to the original folder,
  private JSON-lines audit log (`TCB_AUDIT_FILE`), no BCC/attachments, HTML-escaped bodies,
  no automatic retry for sends.
- Claude Code `headersHelper` script that reads the API key from the Keychain at connect time.
- `scripts/external-check.sh` for testing the public endpoint over the public ingress path.
- Terms-of-service and trademark notes; issue templates; Dependabot; CI actions pinned by SHA.

### Changed
- Licence is now AGPL-3.0-or-later, matching upstream.
- `get_email_metadata` no longer reports `sender_authentication` (Tuta returns null for it at
  the model version used).
- CI secret scan runs the gitleaks CLI over the full history.

## 1.0.0 — 2026-10-18

Initial release: read-only Tuta Mail MCP bridge.

- Seven read-only tools: `list_folders`, `list_emails`, `search_emails`, `read_email`,
  `get_email_metadata`, `get_thread`, `connector_status`.
- Streamable HTTP (stateless, JSON) on a loopback listener behind any TLS proxy (Tailscale
  Funnel documented); `X-API-Key` authentication; tool allowlist enforced in the HTTP guard and
  the MCP registry; Host/Origin checks, body limit, rate limit, timeouts.
- Bounded newest-first folder reading (MailSetEntry paging + load-multiple) instead of
  downloading whole mail bags.
- macOS Keychain or chmod-600 file credentials; TOTP 2FA via a dedicated factor; login backoff.
- launchd service, helper scripts, redacted logging, tests with encrypted synthetic mail.
- Upstream tutaproxy v1.3.18 and TutaMCP v0.1.8 as pinned submodules.
