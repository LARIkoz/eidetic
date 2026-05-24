#!/bin/bash
# AI Memory System v1 — Health Check
set -euo pipefail

echo "=== Memory System Health ==="

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB="$HOME/.claude/memory-system/db/index.db"
CURRENT_FILES=""
CURRENT_CHUNKS=""
if [ -f "$DB" ]; then
    SIZE=$(du -h "$DB" | cut -f1)
    CHUNKS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM memory_chunks" 2>/dev/null || echo "?")
    FILES=$(sqlite3 "$DB" "SELECT COUNT(DISTINCT path) FROM memory_chunks" 2>/dev/null || echo "?")
    CURRENT_FILES="$FILES"
    CURRENT_CHUNKS="$CHUNKS"
    echo "✅ Index: ${SIZE}, ${FILES} files, ${CHUNKS} chunks"
else
    echo "❌ Index missing: $DB"
fi

RULES_FILE="$HOME/.claude/rules/memory-context.md"
if [ -f "$RULES_FILE" ] && [ -n "$CURRENT_FILES" ] && [ -n "$CURRENT_CHUNKS" ]; then
    HEADER=$(sed -n '1,5p' "$RULES_FILE" | grep "_Assembled:" | head -1 || true)
    if [ -n "$HEADER" ]; then
        CONTEXT_FILES=$(printf '%s\n' "$HEADER" | sed -n 's/.*| \([0-9][0-9]*\) files, \([0-9][0-9]*\) chunks indexed.*/\1/p')
        CONTEXT_CHUNKS=$(printf '%s\n' "$HEADER" | sed -n 's/.*| \([0-9][0-9]*\) files, \([0-9][0-9]*\) chunks indexed.*/\2/p')
        if [ "$CONTEXT_FILES" = "$CURRENT_FILES" ] && [ "$CONTEXT_CHUNKS" = "$CURRENT_CHUNKS" ]; then
            echo "✅ Memory context fresh: ${CONTEXT_FILES} files, ${CONTEXT_CHUNKS} chunks"
        else
            echo "⚠️ Memory context stale: context has ${CONTEXT_FILES:-?} files/${CONTEXT_CHUNKS:-?} chunks, index has ${CURRENT_FILES} files/${CURRENT_CHUNKS} chunks"
        fi
    else
        echo "⚠️ Memory context header missing: $RULES_FILE"
    fi
elif [ -f "$DB" ]; then
    echo "⬜ Memory context not assembled yet"
fi

SEARCH_BIN="$HOME/.claude/memory-system/bin/search.sh"
if [ ! -x "$SEARCH_BIN" ] && [ -x "$SCRIPT_DIR/search.sh" ]; then
    SEARCH_BIN="$SCRIPT_DIR/search.sh"
fi

if "$SEARCH_BIN" "test" --limit 1 >/dev/null 2>&1; then
    echo "✅ Search works"
else
    echo "❌ Search broken"
fi

if grep -q "smart-memory-inject" "$HOME/.claude/settings.json" 2>/dev/null; then
    echo "✅ Assembly hook installed"
else
    echo "⬜ Assembly hook not installed yet"
fi

if grep -q "session-signals" "$HOME/.claude/settings.json" 2>/dev/null; then
    echo "✅ Signal hook installed"
else
    echo "⬜ Signal hook not installed yet"
fi

if ls "$HOME/.claude/hooks/"*.bak >/dev/null 2>&1; then
    echo "✅ Hook backups present"
else
    echo "❌ No hook backups"
fi

if [ -f "$HOME/.claude/settings.json.pre-memory-system" ]; then
    echo "✅ Settings backup present"
else
    echo "❌ No settings backup"
fi

if [ -f "$HOME/.claude/skills/memory-recall/SKILL.md" ]; then
    echo "✅ Recall skill installed"
else
    echo "⬜ Recall skill not installed yet"
fi
