# Roadmap

Version 1 is deliberately read-only. Any capability below changes the risk profile and must be
added explicitly — never enabled by upgrading dependencies (the tool allowlist lives in this
repository, not upstream).

Ground rules for every write capability:

- Off by default; enabled by a named setting (e.g. `TCB_ENABLE_DRAFTS=1`) **and** listed in a
  separate allowlist, so the read-only deployment stays the default.
- Separate API key (or OAuth scope) from the read-only key, so a read-only client can never
  escalate.
- Tool annotations (`destructiveHint`, `readOnlyHint=false`) set correctly so clients ask the user.
- Audit log entry (tool, target ids, outcome — never content).
- Tests proving the feature is unreachable when disabled.

| Feature | What is needed |
|---|---|
| Save drafts to Tuta | `TutaClient.create_draft` exists upstream. Add a `save_draft` tool that only creates drafts (no send path), recipients limited to the original thread for replies. Lowest-risk write; good first step. |
| Reply / send | Two-step design: tool returns a draft id; sending requires a separate confirmation that cannot be issued by the same model turn (e.g. a one-time code shown to the user out of band). Recipient allowlist option. Never autonomous. |
| Delete / move / mark read | Separate key + confirmation; prefer move-to-trash over permanent delete; never bulk by default. |
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
