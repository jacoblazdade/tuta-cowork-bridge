#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# One real Tuta login + a 3-message inbox page. Prints counts and booleans only.
source "$(dirname "$0")/lib.sh"
exec "$BIN" live-check
