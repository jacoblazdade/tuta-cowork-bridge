#!/usr/bin/env bash
# Interactive setup: dependencies, credentials (Keychain), API key and settings.
# Secrets are typed into security(1)'s own hidden prompt; they never appear in
# argv, shell history, files in this repository, or this script's output.
#
#   scripts/setup.sh                 full setup
#   scripts/setup.sh --credentials   re-enter Tuta credentials only
#   scripts/setup.sh --rotate-key    generate a new MCP API key
source "$(dirname "$0")/lib.sh"

[[ "$(uname)" == "Darwin" ]] || die "this script targets macOS (see README for other platforms)"
command -v uv >/dev/null || die "uv is required: https://docs.astral.sh/uv/"

mode="${1:-all}"

have_item() { /usr/bin/security find-generic-password -s "$TCB_KEYCHAIN_SERVICE" -a "$1" >/dev/null 2>&1; }

store_prompted() {  # $1 = item name, $2 = human description
  echo
  echo "Enter $2 (input hidden; you will be asked twice)."
  /usr/bin/security add-generic-password -U -s "$TCB_KEYCHAIN_SERVICE" -a "$1" -l "$TCB_KEYCHAIN_SERVICE" -w \
    || die "could not store $1"
  echo "stored $1 in Keychain (service: $TCB_KEYCHAIN_SERVICE)"
}

install_deps() {
  echo "==> Fetching pinned upstream code and dependencies"
  git -C "$REPO_DIR" submodule update --init --recursive
  (cd "$REPO_DIR" && uv sync --frozen --no-dev)
}

credentials() {
  echo "==> Tuta credentials (stored only in your login Keychain)"
  store_prompted TUTA_EMAIL "your Tuta email address"
  store_prompted TUTA_PASSWORD "your Tuta password"
  echo
  echo "Two-factor authentication: unattended login needs the TOTP *seed* of a second factor."
  echo "Recommended: add a NEW, separate TOTP factor in Tuta for this bridge (Settings > Login >"
  echo "Second factor) so it can be revoked independently. Never use your recovery code here."
  read -r -p "Does this Tuta account require TOTP 2FA? [y/N] " answer
  if [[ "$answer" =~ ^[Yy] ]]; then
    store_prompted TUTA_TOTP_SECRET "the base32 TOTP seed of the bridge's second factor"
  elif have_item TUTA_TOTP_SECRET; then
    read -r -p "Remove the stored TOTP seed? [y/N] " rm_seed
    [[ "$rm_seed" =~ ^[Yy] ]] && /usr/bin/security delete-generic-password -s "$TCB_KEYCHAIN_SERVICE" -a TUTA_TOTP_SECRET >/dev/null
  fi
}

api_key() {
  if [[ "$1" == "rotate" ]] || ! have_item TCB_API_KEY; then
    "$BIN" gen-api-key
    echo "Update the key in your MCP clients (see README: 'Retrieve the API key')."
  else
    echo "MCP API key already present (use --rotate-key to replace it)"
  fi
}

settings() {
  echo "==> Settings ($TCB_ENV_FILE)"
  mkdir -p "$TCB_CONFIG_DIR" "$TCB_LOG_DIR"
  chmod 700 "$TCB_CONFIG_DIR" "$TCB_LOG_DIR"
  local ts host=""
  ts="$(tailscale_bin)"
  if [[ -n "$ts" ]]; then
    host="$("$ts" status --json 2>/dev/null | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
  fi
  read -r -p "Public hostname clients will use [${host:-none}]: " entered
  host="${entered:-$host}"
  read -r -p "Local port [$TCB_PORT]: " port
  port="${port:-$TCB_PORT}"
  umask 077
  cat > "$TCB_ENV_FILE" <<EOF
# tuta-cowork-bridge settings (non-secret). Secrets live in the Keychain.
TCB_HOST=127.0.0.1
TCB_PORT=$port
TCB_MCP_PATH=$TCB_MCP_PATH
TCB_PUBLIC_HOSTS=$host
TCB_KEYCHAIN_SERVICE=$TCB_KEYCHAIN_SERVICE
TCB_LOG_LEVEL=INFO
EOF
  echo "wrote $TCB_ENV_FILE"
}

case "$mode" in
  --credentials) credentials ;;
  --rotate-key) api_key rotate ;;
  all)
    install_deps
    settings
    credentials
    api_key keep
    echo "==> Self-check"
    source "$(dirname "$0")/lib.sh"  # reload the settings just written
    "$BIN" check || true
    echo
    echo "Next: scripts/live-check.sh (one login + counts only), then scripts/install-service.sh"
    ;;
  *) die "unknown option $mode" ;;
esac
