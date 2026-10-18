#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Shows recent service logs. Logs are already redacted (tokens, keys, email
# addresses) and never contain mail subjects or bodies.
#   scripts/logs.sh [lines]   |   scripts/logs.sh -f
source "$(dirname "$0")/lib.sh"
if [[ "${1:-}" == "-f" ]]; then
  exec tail -F "$TCB_LOG_DIR/bridge.log" "$TCB_LOG_DIR/stderr.log"
fi
tail -n "${1:-50}" "$TCB_LOG_DIR/bridge.log" 2>/dev/null || echo "(no bridge.log yet)"
if [[ -s "$TCB_LOG_DIR/stderr.log" ]]; then
  echo "--- stderr.log (crash output)"
  tail -n 20 "$TCB_LOG_DIR/stderr.log"
fi
