# Contributing

Thanks for helping. This bridge handles a real, encrypted mailbox, so the bar is: small,
reviewable changes that keep the read-only guarantee.

## Development

```bash
git clone --recurse-submodules <repo-url>
cd tuta-cowork-bridge
uv sync
uv run pytest -q
```

The test suite uses only synthetic data: `tests/fakes.py` serves fake mail encrypted with
tutaproxy's real crypto, and any non-read `TutaClient` method trips an assertion.
**Never use a real account in tests, fixtures, issues or logs.** Live checks
(`scripts/live-check.sh`) print counts only and are not part of CI.

## Rules

- Read tools go in `READ_TOOLS` (annotated `readOnlyHint=True`). Anything that modifies the
  mailbox belongs to an opt-in group in `WRITE_GROUP_TOOLS`, lives in `write.py`, has server-side
  limits, and follows the [ROADMAP.md](ROADMAP.md) ground rules. Always with tests, including one
  proving the tool is unreachable when its group is disabled.
- Never log or return secrets, tokens, or mail content in errors. Add a test when you touch a
  code path that could.
- Upstream (`vendor/`) is pinned. To update: check out the new tag in the submodule, review the
  diff (network calls, logging, crypto, telemetry), run the tests, and describe the review in
  the PR. Generic fixes belong upstream first.
- Keep personal data out of commits: no real addresses, hostnames, usernames or paths.
- New code is AGPL-3.0-or-later, like the rest of the project and upstream.

## Pull requests

Describe what changed and how you verified it. CI runs the test suite and a secret scan.
