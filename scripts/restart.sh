#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Restarts the running agent (picks up a rotated API key or new code).
source "$(dirname "$0")/lib.sh"
launchctl kickstart -k "$DOMAIN/$TCB_LABEL" && echo "restarted $TCB_LABEL"
