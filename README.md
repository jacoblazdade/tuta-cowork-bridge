# tuta-cowork-bridge

Self-hosted MCP bridge connecting **Tuta Mail** to **Claude Cowork** and other remote MCP
clients — **read-only by default**, with opt-in tools for cleaning up and sending mail.

> **Unofficial.** This project is not affiliated with or endorsed by Tuta GmbH. Tuta has no
> public API; this bridge relies on the community reverse-engineered client in
> [tutaproxy](https://github.com/peix2/tutaproxy-public), which can break when Tuta changes its
> protocol.

Out of the box Claude can list, search and read your mail, follow threads and draft replies
**in the conversation**; nothing can be modified. If you opt in (`TCB_WRITE_TOOLS`), Claude can
also move, trash/restore, mark and label mail, create/rename folders and send mail to an
allowlist of recipients — see [Write tools](#write-tools-opt-in). Permanent deletion is never
available. Tools that are not enabled do not exist in the server, and calls to them are
rejected at the HTTP boundary.

```
Claude Cowork (Anthropic cloud)
      │ HTTPS + X-API-Key
      ▼
Tailscale Funnel (public HTTPS, TLS terminated by tailscaled)
      │ http://127.0.0.1:8080/mcp
      ▼
tuta-cowork-bridge ── API-key guard ── 7 read-only MCP tools
      │
      ▼
tutaproxy TutaClient (pinned submodule) ──► Tuta servers
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and [SECURITY.md](SECURITY.md) for the
threat model.

## Tools

| Tool | What it does |
|---|---|
| `list_folders` | Folders and labels (id, name, type) |
| `list_emails` | Newest emails in a folder, metadata only; `unread_only`, cursor paging |
| `search_emails` | Bounded newest-first scan of one folder: sender, recipient, correspondent, subject, date range, unread, attachments, optional body text (≤ 25 bodies per call) |
| `read_email` | Headers, full recipient lists, attachment names, plain-text body (≤ 50 000 chars) |
| `get_email_metadata` | Same without the body |
| `get_thread` | The conversation an email belongs to, optionally with bodies |
| `connector_status` | Version, read-only flag, enabled write groups, Tuta session state |

Reading an email does **not** mark it as read. Custom folders and labels work everywhere a
folder is accepted, by name or `folder_id` (e.g. `search_emails(folder="Freelance", sender="…")`).

### Write tools (opt-in)

Enable groups with `TCB_WRITE_TOOLS` in your private `bridge.env`, then `scripts/install-service.sh`:

| Group | Tools | Notes |
|---|---|---|
| `organize` | `move_emails`, `trash_emails`, `restore_emails`, `mark_emails` | "Delete" = Trash. `restore_emails` returns mail to the folder it was trashed from. Move targets: inbox, archive, spam, custom folders. |
| `folders` | `create_folder`, `rename_folder`, `label_emails` | Custom folders only; no folder deletion. Labels must already exist. |
| `send` | `send_email` | Plain-text mail from your address. Every recipient must match `TCB_SEND_ALLOWLIST` (default `me` = only yourself; also `name@domain`, `@domain`, or `*`). No BCC, no attachments, max 10 recipients. Sent as a new message (not threaded). |

Server-side guardrails regardless of the client: ≤ 50 emails per call, daily caps
(`TCB_DAILY_WRITE_LIMIT` modified emails, `TCB_DAILY_SEND_LIMIT` sends), no automatic retry for
sends, and a private audit log (`TCB_AUDIT_FILE`, JSON lines, ids/actions/recipient domains only —
never subjects, bodies or addresses) that also powers undo and the caps. Write tools carry
`readOnlyHint=false` (and `destructiveHint=true` for trash/send) so clients can ask before running
them. **Inbox rules are not supported** (Tuta's rule storage is not implemented by the client
library); create rules in the Tuta app — Claude can propose them and sort existing mail with
`move_emails`.

## Requirements

- macOS (Apple Silicon or Intel) with a user that stays logged in, or Linux / a container (see
  [Other deployments](#other-deployments))
- [uv](https://docs.astral.sh/uv/), git, Python 3.12–3.13 (uv installs it if needed)
- [Tailscale](https://tailscale.com) with HTTPS certificates and Funnel enabled for the
  machine (for Claude Cowork / claude.ai), or any other TLS reverse proxy you trust
- A Tuta account. With 2FA, add a **separate Authenticator (TOTP) factor** for the bridge
  (passkeys/security keys cannot be used unattended)

## Install (macOS + Tailscale Funnel)

```bash
git clone --recurse-submodules https://github.com/jacoblazdade/tuta-cowork-bridge.git
cd tuta-cowork-bridge
scripts/setup.sh            # deps, settings, credentials into the Keychain, API key
scripts/live-check.sh       # one Tuta login, prints counts only
scripts/install-service.sh  # launchd agent, loopback listener on 127.0.0.1:8080
```

`setup.sh` asks for your Tuta email, password and (optionally) a TOTP seed at `security`'s
hidden prompt; values go straight into your login Keychain (service `tuta-cowork-bridge`). If
you add a new TOTP factor in Tuta, confirm it with the code from `scripts/totp-code.sh`.

Publish through Funnel. If another service already uses `/` on port 443, mount the bridge
under a prefix — Tailscale strips it before proxying:

```bash
scripts/funnel.sh on --path /tuta     # → https://<machine>.<tailnet>.ts.net/tuta/mcp
scripts/external-check.sh https://<machine>.<tailnet>.ts.net/tuta/mcp
```

`funnel.sh off --path /tuta` removes only the bridge's route. Do **not** use
`tailscale funnel --https=443 off`: it removes every route on that port.

### Retrieve the API key

Copy it to the clipboard without displaying it, paste it where needed, then clear the
clipboard:

```bash
security find-generic-password -s tuta-cowork-bridge -a TCB_API_KEY -w | pbcopy
pbcopy < /dev/null
```

### Connect Claude Cowork / claude.ai

1. Claude → **Customize → Connectors → + Add → Add custom connector**
   (Team/Enterprise: an owner adds it under *Organization settings → Connectors*).
2. Name: `Tuta Mail (Self-Hosted)`; URL: `https://<machine>.<tailnet>.ts.net/tuta/mcp`.
3. Authentication: no OAuth / no sign-in. Add a **request header** `X-API-Key` with the key.
4. Enable the connector in a Cowork session (connector/tools menu) and ask e.g.
   *"Show me my latest 10 emails."*

Connections come from Anthropic's cloud, so the URL must be publicly reachable — a
tailnet-only `tailscale serve` URL is not enough.

### Connect Claude Code (same machine)

Uses the running service over loopback; the key is fetched from the Keychain at connect
time by a `headersHelper`, so it is never written to Claude Code's config:

```bash
claude mcp add-json --scope user tuta-mail \
  "{\"type\":\"http\",\"url\":\"http://127.0.0.1:8080/mcp\",\"headersHelper\":\"$PWD/scripts/claude-code-headers.sh\"}"
claude mcp get tuta-mail
```

For a client without header support, `tuta-cowork-bridge stdio` serves the same tools over
stdio (it logs in to Tuta separately).

## Operating

| Task | Command |
|---|---|
| Health (launchd, listener, authenticated probe, Funnel routes) | `scripts/status.sh` |
| Start / stop / restart | `scripts/start.sh` · `scripts/stop.sh` · `scripts/restart.sh` |
| Logs (redacted, no mail content) | `scripts/logs.sh [lines]` · `scripts/logs.sh -f` |
| Public endpoint check | `scripts/external-check.sh <url>` |
| Rotate the API key | `scripts/setup.sh --rotate-key && scripts/restart.sh`, then update clients |
| Re-enter Tuta credentials | `scripts/setup.sh --credentials && scripts/restart.sh` |
| Tests (synthetic data only) | `scripts/test.sh` |
| Uninstall the service | `scripts/install-service.sh --remove` |
| Review write actions | `tail ~/Library/Application\ Support/tuta-cowork-bridge/audit.jsonl` |

Private state lives outside the repository: settings in
`~/Library/Application Support/tuta-cowork-bridge/bridge.env`, logs in
`~/Library/Logs/tuta-cowork-bridge/`, the agent in
`~/Library/LaunchAgents/io.github.tuta-cowork-bridge.plist`, secrets in the Keychain.

## Configuration

All settings are `TCB_*` environment variables; see [.env.example](.env.example). Highlights:
`TCB_HOST`/`TCB_PORT`/`TCB_MCP_PATH` (listener, loopback enforced), `TCB_PUBLIC_HOSTS`
(Host-header allowlist), `TCB_CREDENTIAL_BACKEND` (`keychain` or `file`),
`TCB_RATE_PER_MINUTE`, `TCB_TOOL_TIMEOUT`, `TCB_SOURCE_URL` (source link shown by
`connector_status`; keep it pointing at the code you run — AGPL §13).

## Other deployments

The application only needs a loopback (or container-internal) listener and an HTTPS proxy
that forwards the `X-API-Key` header:

- **Linux / systemd:** `TCB_CREDENTIAL_BACKEND=file` with `TCB_CREDENTIALS_FILE` pointing to a
  `chmod 600` file outside the repo (`TUTA_EMAIL`, `TUTA_PASSWORD`, `TUTA_TOTP_SECRET`,
  `TCB_API_KEY`), run `tuta-cowork-bridge serve`, front it with Tailscale Funnel, Caddy, etc.
- **Container:** mount the credentials file read-only, set `TCB_HOST=0.0.0.0` and
  `TCB_ALLOW_NON_LOOPBACK=1`, and publish the port on `127.0.0.1` only.

## Limitations

- **Unofficial protocol.** Tuta can reject the pinned client version (HTTP 474) or change its
  model; updating means moving the tutaproxy submodule to a newer reviewed release.
- **No server-side search.** Tuta's search index lives in its clients. `search_emails` scans one
  folder newest-first (default 300, max 2000 messages per call, continue with `next_cursor`).
  Body search fetches at most 25 bodies per call. No full-text index is built.
- `recipient` matches the **first** recipient only (full lists come from `read_email`).
- Attachments: names and types only; contents are never downloaded.
- Threads list up to 100 conversation entries.
- After a macOS reboot with FileVault, nothing runs until the user logs in (the login Keychain
  and the per-user launchd agent need the session). Tailscale's GUI app has the same
  requirement.
- `kill -9`/power loss skips the Tuta logout; that session expires server-side.
- One Tuta session per bridge process; the API key is read at startup (restart after rotation).
- Write tools: no inbox rules, no folder deletion, no permanent deletion, no drafts, no
  attachments; `send_email` does not thread replies into the original conversation.

## Acknowledgements and licence

Built on [tutaproxy](https://github.com/peix2/tutaproxy-public) (Tuta API client and crypto) and
[TutaMCP](https://github.com/peix2/tutamcp-public) (mail decryption helpers) by peix2, both
AGPL-3.0, used unmodified as pinned git submodules. Neither project's MCP tools are registered.

Licensed under **AGPL-3.0-only** ([LICENSE](LICENSE)). If you run a modified version for others
over a network, you must offer them its source (set `TCB_SOURCE_URL`).
