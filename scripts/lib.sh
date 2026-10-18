# SPDX-License-Identifier: AGPL-3.0-or-later
# Shared settings for the helper scripts. Sourced, not executed.
# All private deployment state lives outside the repository:
#   config:  $TCB_CONFIG_DIR/bridge.env   (non-secret settings)
#   logs:    $TCB_LOG_DIR/
#   service: ~/Library/LaunchAgents/$TCB_LABEL.plist
#   secrets: macOS Keychain, service $TCB_KEYCHAIN_SERVICE

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TCB_CONFIG_DIR="${TCB_CONFIG_DIR:-$HOME/Library/Application Support/tuta-cowork-bridge}"
TCB_ENV_FILE="$TCB_CONFIG_DIR/bridge.env"

# Load non-secret settings (KEY=VALUE lines) without executing the file; the file wins.
if [[ -f "$TCB_ENV_FILE" ]]; then
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^TCB_[A-Z_]+$ ]] || continue
    export "$key=$value"
  done < "$TCB_ENV_FILE"
fi

TCB_LABEL="${TCB_LABEL:-io.github.tuta-cowork-bridge}"
TCB_LOG_DIR="${TCB_LOG_DIR:-$HOME/Library/Logs/tuta-cowork-bridge}"
TCB_KEYCHAIN_SERVICE="${TCB_KEYCHAIN_SERVICE:-tuta-cowork-bridge}"
TCB_PORT="${TCB_PORT:-8080}"
TCB_MCP_PATH="${TCB_MCP_PATH:-/mcp}"
PLIST="$HOME/Library/LaunchAgents/$TCB_LABEL.plist"
BIN="$REPO_DIR/.venv/bin/tuta-cowork-bridge"
DOMAIN="gui/$(id -u)"

die() { echo "error: $*" >&2; exit 1; }

tailscale_bin() {
  if command -v tailscale >/dev/null 2>&1 && tailscale version >/dev/null 2>&1; then
    command -v tailscale
  elif [[ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]]; then
    echo /Applications/Tailscale.app/Contents/MacOS/Tailscale
  fi
}

# Sends one authenticated JSON-RPC request to the local endpoint. The API key is
# read from the Keychain and handed to curl on stdin (-H @-), never via argv.
local_rpc() {
  local body="$1" url="${2:-http://127.0.0.1:$TCB_PORT$TCB_MCP_PATH}"
  /usr/bin/security find-generic-password -s "$TCB_KEYCHAIN_SERVICE" -a TCB_API_KEY -w \
    | sed 's/^/X-API-Key: /' \
    | curl -sS --max-time 60 -H @- -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-06-18' \
        -d "$body" "$url"
}
