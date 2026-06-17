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
VEC_PATH="$MEMORY_SYSTEM/db/vectors.db"

python3 "${SCRIPT_DIR}/index_impl.py" "$MODE" "$DB_PATH"

# A FULL reindex rebuilds vectors too. FTS-only would leave embeddings and the
# content-hash scheme stale, and the search-time guard would then (correctly)
# degrade vector search to FTS until a real vector rebuild. Needs fastembed; the
# embed lock makes this safe against the session-start incremental embed.
if [ "$MODE" = "--full" ] && python3 -c "import fastembed" >/dev/null 2>&1; then
    python3 "${SCRIPT_DIR}/embed.py" "$DB_PATH" "$VEC_PATH" --full
fi
