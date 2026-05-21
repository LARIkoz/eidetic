#!/bin/bash
# AI Memory System v1 — Smart Memory Inject (SessionStart hook)
# Writes assembled memories to ~/.claude/rules/memory-context.md
# Claude auto-loads rules/ files — no 10K stdout cap.
# P3: ALL feedback rules always included. P9: only relevant, not dump.
set -euo pipefail

LOCKDIR="$HOME/.claude/memory-system/.memory.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    LOCK_AGE=$(( $(date +%s) - $(stat -f%m "$LOCKDIR" 2>/dev/null || echo 0) ))
    if [ "$LOCK_AGE" -gt 30 ]; then rm -r "$LOCKDIR" 2>/dev/null; mkdir "$LOCKDIR" 2>/dev/null || true; fi
    if [ ! -d "$LOCKDIR" ]; then echo "Memory system busy, skipping"; exit 0; fi
fi
trap 'rm -r "$LOCKDIR" 2>/dev/null' EXIT

MEMORY_SYSTEM="$HOME/.claude/memory-system"
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

# Record session + get phase-adaptive hint
SESSION_HINT=$(python3 "$MEMORY_SYSTEM/bin/session_counter.py" "$(pwd)" "record-and-hint" 2>/dev/null || echo "")

# Assemble context via Python (complex logic)
python3 "$MEMORY_SYSTEM/bin/assemble_context.py" "$DB" "$RULES_FILE" "$(pwd)"

# Append session hint to memory-context.md
if [ -n "$SESSION_HINT" ] && [ -f "$RULES_FILE" ]; then
    echo "" >> "$RULES_FILE"
    echo "## Session Awareness" >> "$RULES_FILE"
    echo "" >> "$RULES_FILE"
    echo "$SESSION_HINT" >> "$RULES_FILE"
fi

echo "Memory context updated (with session hint)"
