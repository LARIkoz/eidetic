#!/bin/bash
# AI Memory System v1 — Lint
# Detects orphans, stale files, broken wikilinks
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALLED_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -n "${EIDETIC_MEMORY_SYSTEM:-}" ]; then
    MEMORY_SYSTEM="$EIDETIC_MEMORY_SYSTEM"
elif [ -f "$INSTALLED_ROOT/.installed.json" ]; then
    MEMORY_SYSTEM="$INSTALLED_ROOT"
else
    MEMORY_SYSTEM="$HOME/.claude/memory-system"
fi
DB_PATH="$MEMORY_SYSTEM/db/index.db"

exec python3 "${SCRIPT_DIR}/lint_impl.py" "$DB_PATH" "$@"
