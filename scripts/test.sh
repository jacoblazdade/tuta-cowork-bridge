#!/usr/bin/env bash
# Runs the offline test suite (synthetic data only; never touches a real account).
source "$(dirname "$0")/lib.sh"
cd "$REPO_DIR"
exec uv run --frozen pytest -q "$@"
