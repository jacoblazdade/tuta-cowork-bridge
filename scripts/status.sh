#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Health overview: launchd state, listener, authenticated MCP probe, Tailscale route.
# Prints no secrets and no mail content.
source "$(dirname "$0")/lib.sh"

echo "== launchd ($TCB_LABEL)"
tmp="$(mktemp)"
if launchctl print "$DOMAIN/$TCB_LABEL" >"$tmp" 2>/dev/null; then
  grep -E '^[[:space:]]+(state|pid|last exit code|runs) =' "$tmp" | sed 's/^[[:space:]]*/  /' || true
else
  echo "  not loaded"
fi
rm -f "$tmp"

echo "== listener"
lsof -nP -iTCP:"$TCB_PORT" -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print "  " $1, $9}' || true

echo "== local MCP probe"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -X POST "http://127.0.0.1:$TCB_PORT$TCB_MCP_PATH" || true)
echo "  unauthenticated request -> HTTP $code (expect 401)"
reply=$(local_rpc '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"connector_status","arguments":{}}}' 2>/dev/null || true)
printf '%s' "$reply" | /usr/bin/python3 -c '
import json, sys
try:
    r = json.load(sys.stdin)["result"]
    d = r.get("structuredContent") or json.loads(r["content"][0]["text"])
    print("  authenticated: ok | version", d["version"], "| read_only", d["read_only"], "| tools", len(d["tools"]))
    print("  tuta session:", json.dumps(d["tuta_session"]))
except Exception:
    print("  authenticated probe failed (service down or key missing)")
'

echo "== tailscale"
ts="$(tailscale_bin)"
if [[ -n "$ts" ]]; then
  "$ts" funnel status 2>/dev/null | sed 's/^/  /' | grep -v '^[[:space:]]*$' || true
else
  echo "  tailscale not found"
fi
