#!/bin/bash
# AI Memory System v1 — Smart Memory Inject (SessionStart hook)
# Writes assembled memories to ~/.claude/rules/memory-context.md
# Claude auto-loads rules/ files — no 10K stdout cap.
# P3: ALL feedback rules always included. P9: only relevant, not dump.
set -euo pipefail

MEMORY_SYSTEM="$HOME/.claude/memory-system"

acquire_memory_lock() {
    local lockdir="$MEMORY_SYSTEM/.memory.lock"
    mkdir -p "$MEMORY_SYSTEM"
    if mkdir "$lockdir" 2>/dev/null; then
        printf '%s\n' "$$" > "$lockdir/pid"
        trap 'rm -rf "$MEMORY_SYSTEM/.memory.lock"' EXIT
        return 0
    fi

    local old_pid=""
    old_pid=$(cat "$lockdir/pid" 2>/dev/null || true)
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
        echo "Memory system busy (PID $old_pid alive), skipping"
        return 1
    fi
    rm -rf "$lockdir"
    if mkdir "$lockdir" 2>/dev/null; then
        printf '%s\n' "$$" > "$lockdir/pid"
        trap 'rm -rf "$MEMORY_SYSTEM/.memory.lock"' EXIT
        return 0
    fi
    echo "Memory system busy, skipping"
    return 1
}

acquire_memory_lock || exit 0

DB="$MEMORY_SYSTEM/db/index.db"
RULES_FILE="$HOME/.claude/rules/memory-context.md"
SEARCH="$MEMORY_SYSTEM/bin/search.sh"
INDEX="$MEMORY_SYSTEM/bin/index.sh"

# Fallback: if memory system not ready, use MEMORY.md head
if [ ! -f "$DB" ] || [ ! -f "$SEARCH" ]; then
    # Find first MEMORY.md
    for f in "$HOME"/.claude/projects/*/memory/MEMORY.md; do
        if [ -f "$f" ]; then
            head -200 "$f" > "$RULES_FILE" 2>/dev/null || true
            echo "Memory context: fallback to MEMORY.md (index not ready)"
            exit 0
        fi
    done
    echo "Memory context: no index, no MEMORY.md"
    exit 0
fi

# Incremental reindex (< 500ms)
"$INDEX" --incremental >/dev/null 2>&1 || true

# Drift detection (24h throttle, crash-guarded — B6: must not kill injection)
python3 "$MEMORY_SYSTEM/bin/drift_check.py" "$DB" 2>/dev/null || true

# Code indexing for CWD project (if tree-sitter available)
if python3 -c "import tree_sitter" 2>/dev/null; then
    python3 -c "
import subprocess, sys
try:
    subprocess.run([sys.executable, '$MEMORY_SYSTEM/bin/code_index.py', '$DB', '$(pwd)'],
                   timeout=10, capture_output=True)
except subprocess.TimeoutExpired:
    pass
" 2>/dev/null || true
fi

# Incremental vector embeddings (if fastembed available)
VECTORS_DB="$MEMORY_SYSTEM/db/vectors.db"
if [ -f "$VECTORS_DB" ]; then
    python3 -c "
import subprocess, sys
try:
    subprocess.run([sys.executable, '$MEMORY_SYSTEM/bin/embed.py', '$DB', '$VECTORS_DB'],
                   timeout=30, capture_output=True)
except subprocess.TimeoutExpired:
    pass
" 2>/dev/null || true
fi

# Record session + get phase-adaptive hint
SESSION_HINT=$(python3 "$MEMORY_SYSTEM/bin/session_counter.py" "$(pwd)" "record-and-hint" 2>/dev/null || echo "")

# Assemble context via Python (crash-guarded — B6/H4: must not kill injection)
python3 "$MEMORY_SYSTEM/bin/assemble_context.py" "$DB" "$RULES_FILE" "$(pwd)" || true

# Append session hint to memory-context.md
if [ -n "$SESSION_HINT" ] && [ -f "$RULES_FILE" ]; then
    echo "" >> "$RULES_FILE"
    echo "## Session Awareness" >> "$RULES_FILE"
    echo "" >> "$RULES_FILE"
    echo "$SESSION_HINT" >> "$RULES_FILE"
fi

# Check for updates (background, non-blocking)
UPDATE_CHECK="$MEMORY_SYSTEM/bin/check-update.sh"
if [ -f "$UPDATE_CHECK" ]; then
    bash "$UPDATE_CHECK" 2>/dev/null &
fi

echo "Memory context updated (with session hint)"
