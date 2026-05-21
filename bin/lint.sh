#!/bin/bash
# AI Memory System v1 — Lint
# Detects orphans, stale files, broken wikilinks
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_PATH="$HOME/.claude/memory-system/db/index.db"

exec python3 "${SCRIPT_DIR}/lint_impl.py" "$DB_PATH" "$@"
