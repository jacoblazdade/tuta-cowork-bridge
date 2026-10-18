#!/usr/bin/env bash
# Publishes the bridge through Tailscale Funnel (public HTTPS) or removes it.
# Only the bridge's own route is added/removed; other Serve/Funnel routes are untouched.
#
#   scripts/funnel.sh on  [--port 443|8443|10000] [--path /tuta]
#   scripts/funnel.sh off [--port ...] [--path ...]
#   scripts/funnel.sh status
#
# --path mounts the bridge under a prefix, for when another service already owns "/"
# on the same port. Tailscale strips the prefix before proxying, so the public URL is
# https://<host><path><TCB_MCP_PATH>.
source "$(dirname "$0")/lib.sh"
ts="$(tailscale_bin)"; [[ -n "$ts" ]] || die "tailscale not found"

action="${1:-status}"; shift || true
port=443; path=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --path) path="$2"; shift 2 ;;
    *) die "unknown argument $1" ;;
  esac
done
args=(--bg --https="$port")
[[ -n "$path" ]] && args+=(--set-path="$path")
target="http://127.0.0.1:$TCB_PORT"

case "$action" in
  on)
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -X POST "$target$TCB_MCP_PATH" || true)
    [[ "$code" == "401" ]] || die "local bridge must be running and rejecting unauthenticated requests first (got $code)"
    "$ts" funnel "${args[@]}" "$target"
    ;;
  off)
    "$ts" funnel "${args[@]}" "$target" off
    ;;
  status) ;;
  *) die "usage: funnel.sh on|off|status" ;;
esac
"$ts" funnel status
