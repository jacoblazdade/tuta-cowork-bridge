#!/usr/bin/env bash
# Verifies the public endpoint over the public internet path: resolves the hostname via
# public DNS (1.1.1.1) and pins curl to that address, so requests go through the HTTPS
# ingress (e.g. Tailscale Funnel relay) rather than a private/tailnet route.
# Prints status codes and tool names only - never the key or mail content.
#
#   scripts/external-check.sh https://host.example.ts.net/tuta/mcp
source "$(dirname "$0")/lib.sh"

url="${1:?usage: external-check.sh <public MCP URL>}"
host="$(printf '%s' "$url" | sed -E 's#^https://([^/:]+).*#\1#')"
ip="$(dig +short @1.1.1.1 "$host" A | grep -E '^[0-9.]+$' | head -1)"
[[ -n "$ip" ]] || die "no public A record for $host"
pin=(--resolve "$host:443:$ip")
echo "public address of $host: $ip"

status() { curl -s "${pin[@]}" -o /dev/null -w '%{http_code}' --max-time 30 "$@"; }
check() {  # name expected actual
  if [[ "$2" == "$3" ]]; then echo "  PASS $1 ($3)"; else echo "  FAIL $1 (expected $2, got $3)"; fail=1; fi
}
authed() {  # $1 = JSON body; key goes to curl via stdin
  /usr/bin/security find-generic-password -s "$TCB_KEYCHAIN_SERVICE" -a TCB_API_KEY -w \
    | sed 's/^/X-API-Key: /' \
    | curl -s "${pin[@]}" --max-time 60 -H @- -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-06-18' \
        -w '\n%{http_code}' -d "$1" "$url"
}
fail=0

echo "unauthenticated / invalid credentials:"
check "POST no key" 401 "$(status -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' "$url")"
check "GET no key" 401 "$(status "$url")"
check "POST wrong key" 401 "$(status -X POST -H 'X-API-Key: wrong' -d '{}' "$url")"
check "POST empty key" 401 "$(status -X POST -H 'X-API-Key: ' -d '{}' "$url")"
plain="$(curl -s --resolve "$host:80:$ip" -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 10 "http://$host/" || true)"
case "$plain" in
  000*|30[1278]\ https://*) check "plain HTTP refused or redirected to https" ok ok ;;
  *) check "plain HTTP refused or redirected to https" "000 or 30x->https" "$plain" ;;
esac

echo "authenticated:"
out="$(authed '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"external-check","version":"1"}}}')"
check "initialize" 200 "${out##*$'\n'}"
out="$(authed '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}')"
check "tools/list" 200 "${out##*$'\n'}"
printf '%s' "${out%$'\n'*}" | /usr/bin/python3 -c 'import json,sys; print("  tools:", ", ".join(sorted(t["name"] for t in json.load(sys.stdin)["result"]["tools"])))'
for tool in tuta_mail_send tuta_mail_delete tuta_mail_mark tuta_mail_move tuta_drive_upload; do
  out="$(authed "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"$tool\",\"arguments\":{}}}")"
  check "write tool $tool blocked" 403 "${out##*$'\n'}"
done
out="$(authed '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"connector_status","arguments":{}}}')"
check "connector_status call" 200 "${out##*$'\n'}"

exit $fail
