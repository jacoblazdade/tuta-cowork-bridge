#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Runs the offline test suite (synthetic data only; never touches a real account).
source "$(dirname "$0")/lib.sh"
cd "$REPO_DIR"
exec uv run --frozen pytest -q "$@"
