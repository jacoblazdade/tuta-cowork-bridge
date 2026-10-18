#!/usr/bin/env bash
# One real Tuta login + a 3-message inbox page. Prints counts and booleans only.
source "$(dirname "$0")/lib.sh"
exec "$BIN" live-check
