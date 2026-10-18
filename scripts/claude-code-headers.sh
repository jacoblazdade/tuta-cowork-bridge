#!/usr/bin/env bash
# headersHelper for Claude Code: prints {"X-API-Key": "..."} read from the Keychain at
# connect time, so the key is never stored in Claude Code's config files.
source "$(dirname "$0")/lib.sh"
key="$(/usr/bin/security find-generic-password -s "$TCB_KEYCHAIN_SERVICE" -a TCB_API_KEY -w)"
[[ "$key" =~ ^[A-Za-z0-9_-]+$ ]] || die "unexpected API key format"
printf '{"X-API-Key": "%s"}\n' "$key"
