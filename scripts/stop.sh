#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Stops the agent until the next start.sh or login (the plist stays installed).
source "$(dirname "$0")/lib.sh"
launchctl bootout "$DOMAIN/$TCB_LABEL" 2>/dev/null && echo "stopped $TCB_LABEL" || echo "$TCB_LABEL was not running"
