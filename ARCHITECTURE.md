# Architecture

```
MCP client (Claude Cowork, claude.ai, Claude Desktop/Code, …)
   │  HTTPS, Streamable HTTP (stateless, JSON responses), header X-API-Key
   ▼
HTTPS ingress (Tailscale Funnel by default; any TLS reverse proxy works)
   │  http://127.0.0.1:<port>/<path>
   ▼
┌──────────────────────── tuta-cowork-bridge (one Python process) ────────────────────────┐
│ http.Guard (ASGI)   auth → rate limit → path → Origin → body cap → tool allowlist      │
│ MCP SDK app         Host/Origin checks, JSON-RPC, protocol negotiation                  │
│ tools.py            7 read-only tools, timeouts, concurrency cap, sanitized errors      │
│ mail.MailReader     bounded scans, filters, read/metadata/thread                        │
│ session.TutaSession one Tuta login, re-login on expiry, login backoff                  │
│ credentials         Keychain / chmod-600 file                                           │
└──────────────────────────────────────────────────────────────────────────────────────────┘
   │  vendor/tutaproxy-public  `tuta` (TutaClient, crypto)        ← pinned submodule
   │  vendor/tutamcp-public    `tutamcp.tools_mail` decrypt helpers ← pinned submodule
   ▼
Tuta (app.tuta.com, blob servers)
```

## Decisions

**One process, Python.** tutaproxy and TutaMCP are Python, and the official MCP Python SDK
serves Streamable HTTP natively. The suggested alternative — TutaMCP over stdio behind the
Node.js `mcp-proxy` — would add a second runtime, a child process and still require a separate
filtering layer to remove TutaMCP's write tools. mcp-proxy was evaluated (v6.7.20) and not used.

**Own tools instead of filtering upstream tools.** Upstream registers write tools
unconditionally and new upstream versions may add more. Filtering by name is fragile; defining
the tool surface here means an upstream upgrade can never add a capability. The allowlist is
enforced twice: the registry is verified at startup, and the HTTP guard rejects `tools/call`
for any other name before the MCP layer sees it.

**Upstream code as pinned submodules, not copies.** Avoids copying AGPL code, keeps
attribution clear, and makes upgrades explicit (`git -C vendor/... checkout <tag>` + review).
Only these upstream symbols are used: `TutaClient` (read methods: login, logout,
get_folders, get_mail_group_key, get_single_mail, get_mail_details, get_file and the generic
`_get_tutanota`/`_get_mailset_entries` GET helpers), `Session`, `TutaAPIError`, and TutaMCP's
`_build_mail_summary`, `_read_mail_full`, `_decrypt_folder_name`, `_FOLDER_TYPE_CLASS`.

**Stateless Streamable HTTP with JSON responses.** No server-side MCP sessions to lose on
restart; each request is independent. The Tuta session is process-wide and lazily created.

**Loopback listener + external TLS.** The bridge never terminates TLS or listens publicly.
Tailscale Funnel provides a public HTTPS name and certificate without router changes;
`--set-path` lets it share port 443 with other services (Tailscale strips the prefix).

**launchd user agent (macOS).** Needs the user's login session for the Keychain, same as the
Tailscale GUI app. `KeepAlive` restarts crashes (throttled to 30 s); logs rotate in-process.

## Reading mail without downloading the folder

Tuta's mailbox model: a folder (MailSet) owns a list of *MailSetEntries* whose ids sort by
received date; each entry points to a Mail in a *mail bag*. The bridge:

1. fetches one page of entries newest-first (`GET mailsetentry/<list>?start=<cursor>&reverse=true`,
   at most 100 per page, never more than the request needs),
2. loads exactly the referenced mails with Tuta's load-multiple (`GET mail/<bag>?ids=a,b,…`,
   ≤ 100 ids; falls back to single loads on error),
3. decrypts metadata locally and applies filters, stopping at the limit, the scan budget or the
   `since` date. The last consumed entry id is returned as `next_cursor`.

Bodies are fetched only by `read_email`, `get_email_metadata` (recipients live in the same
blob; the body is discarded), `get_thread(include_bodies=true)` and `body_contains` search
(≤ 25 per call). Threads use `Mail.conversationEntry` → `conversationentry/<list>`.

Folder names are cached in memory for 5 minutes. Nothing is written to disk.

## Failure handling

| Situation | Behaviour |
|---|---|
| Tuta 401/440 (session expired) | drop session, log in again, retry once |
| Network error | retry once after 1 s, then a sanitized error to the client |
| Login failure | exponential backoff per reason (credentials 5 min → 1 h, network 15 s, 429 2 min) |
| Tuta 474 (client too old) | error telling the owner an upstream update is needed |
| Keychain locked / missing items | API key missing → process exits and launchd retries every 30 s; Tuta items missing → tool calls return a configuration error |
| Crash | launchd restarts the agent |
