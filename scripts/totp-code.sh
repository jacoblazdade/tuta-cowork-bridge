#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Prints the current TOTP code from the stored seed, to confirm the bridge's new
# second factor in Tuta's settings. The seed itself is never printed.
source "$(dirname "$0")/lib.sh"
exec "$BIN" totp-code
