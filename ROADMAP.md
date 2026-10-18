# Roadmap

Read-only is the default. Version 1.1 added opt-in write groups (`organize`, `folders`, `send`)
following the rules below. Every capability changes the risk profile and must be enabled
explicitly — never by upgrading dependencies (the tool allowlist lives in this repository).

Ground rules for every write capability:

- Off by default; enabled by a named group in `TCB_WRITE_TOOLS` **and** listed in the tool
  allowlist, so the read-only deployment stays the default.
- Server-side limits that hold regardless of the client (batch size, daily caps, allowlists).
- Tool annotations (`destructiveHint`, `readOnlyHint=false`) set correctly so clients ask the user.
- Audit log entry (tool, target ids, outcome — never content).
- Tests proving the feature is unreachable when disabled.

| Feature | What is needed |
|---|---|
| ~~Move / trash / restore / mark / labels / create & rename folders~~ | Done in 1.1 (`organize`, `folders`). |
| ~~Send~~ | Done in 1.1 (`send`) with a recipient allowlist, daily cap and no retry. Next: optional out-of-band confirmation. |
| Inbox rules | Not implemented upstream: requires reverse-engineering Tuta's encrypted rule storage (TutanotaProperties); a bug could break existing rules. |
| Save drafts to Tuta | `TutaClient.create_draft` exists upstream. Add a `save_draft` tool that only creates drafts (no send path), recipients limited to the original thread for replies. Lowest-risk write; good first step. |
| Out-of-band send confirmation | Sending requires a confirmation the model cannot issue itself (e.g. a code shown on another device), so the allowlist could be relaxed safely. |
| Separate write key / OAuth scopes | A second API key (or OAuth scopes) for write tools, so read-only clients can never escalate. |
| Body full-text search | Optional local index in an encrypted store (key in Keychain), explicit opt-in, documented retention and rebuild; or bounded on-demand scans as today. |
| Attachments | Return small text attachments inline with size/type limits; never write to disk; treat as untrusted (parsers are an attack surface). |
| Calendar / contacts | Read-only first, using existing upstream read helpers; same allowlist pattern. |
| OAuth instead of a static key | MCP authorization (OAuth 2.1) so keys can be per-client and revocable without rotation. |

Also worth contributing upstream (TutaMCP / tutaproxy):

- Bounded folder listing via MailSetEntry paging + load-multiple instead of downloading whole
  mail bags (`get_mails_in_folder`).
- An option to register only read-only tools, and not registering `reply` when the policy is
  meant to be read-only.
- Redacting access tokens in URLs before logging.
