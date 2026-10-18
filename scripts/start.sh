#!/usr/bin/env bash
# Starts the launchd agent (installs it first if needed).
source "$(dirname "$0")/lib.sh"
[[ -f "$PLIST" ]] || exec "$(dirname "$0")/install-service.sh"
launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null || launchctl kickstart "$DOMAIN/$TCB_LABEL"
echo "started $TCB_LABEL"
