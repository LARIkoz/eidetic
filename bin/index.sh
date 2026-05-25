#!/bin/bash
# AI Memory System v1 — FTS5 Indexer
# Usage: index.sh [--full|--incremental]
# Zero external deps: python3 stdlib + sqlite3
set -euo pipefail

MODE="${1:---incremental}"
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

exec python3 "${SCRIPT_DIR}/index_impl.py" "$MODE" "$DB_PATH"
