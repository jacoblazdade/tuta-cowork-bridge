#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Installs (or refreshes) the per-user launchd agent that runs the bridge.
#   scripts/install-service.sh            install + start
#   scripts/install-service.sh --remove   stop + remove the agent (keeps Keychain items)
source "$(dirname "$0")/lib.sh"

if [[ "${1:-}" == "--remove" ]]; then
  launchctl bootout "$DOMAIN/$TCB_LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed $TCB_LABEL"
  exit 0
fi

[[ -f "$TCB_ENV_FILE" ]] || die "run scripts/setup.sh first"
[[ -x "$BIN" ]] || die "missing $BIN; run scripts/setup.sh first"
mkdir -p "$TCB_LOG_DIR" "$(dirname "$PLIST")"
chmod 700 "$TCB_LOG_DIR"

xml_escape() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

env_xml=""
while IFS='=' read -r key value; do
  [[ "$key" =~ ^TCB_[A-Z_]+$ ]] || continue
  env_xml+="    <key>$key</key><string>$(printf '%s' "$value" | xml_escape)</string>"$'\n'
done < "$TCB_ENV_FILE"

umask 077
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$TCB_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(printf '%s' "$BIN" | xml_escape)</string>
    <string>serve</string>
  </array>
  <key>WorkingDirectory</key><string>$(printf '%s' "$REPO_DIR" | xml_escape)</string>
  <key>EnvironmentVariables</key>
  <dict>
$env_xml    <key>TCB_LOG_FILE</key><string>$(printf '%s' "$TCB_LOG_DIR/bridge.log" | xml_escape)</string>
    <key>TUTAPROXY_TELEMETRY</key><string>false</string>
    <key>PYTHONDONTWRITEBYTECODE</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Standard</string>
  <key>SoftResourceLimits</key>
  <dict><key>NumberOfFiles</key><integer>1024</integer></dict>
  <key>StandardOutPath</key><string>$(printf '%s' "$TCB_LOG_DIR/stdout.log" | xml_escape)</string>
  <key>StandardErrorPath</key><string>$(printf '%s' "$TCB_LOG_DIR/stderr.log" | xml_escape)</string>
</dict>
</plist>
EOF
plutil -lint "$PLIST" >/dev/null

launchctl bootout "$DOMAIN/$TCB_LABEL" 2>/dev/null || true
for _ in $(seq 1 20); do  # bootout is asynchronous; bootstrap fails while the old job lingers
  launchctl print "$DOMAIN/$TCB_LABEL" >/dev/null 2>&1 || break
  sleep 0.5
done
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl enable "$DOMAIN/$TCB_LABEL"
echo "installed and started $TCB_LABEL"
sleep 3
"$(dirname "$0")/status.sh"
