#!/bin/bash
# AI Memory System v1 — Health Check
set -euo pipefail

echo "=== Memory System Health ==="

DB="$HOME/.claude/memory-system/db/index.db"
if [ -f "$DB" ]; then
    SIZE=$(du -h "$DB" | cut -f1)
    CHUNKS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM memory_chunks" 2>/dev/null || echo "?")
    FILES=$(sqlite3 "$DB" "SELECT COUNT(DISTINCT path) FROM memory_chunks" 2>/dev/null || echo "?")
    echo "✅ Index: ${SIZE}, ${FILES} files, ${CHUNKS} chunks"
else
    echo "❌ Index missing: $DB"
fi

if "$HOME/.claude/memory-system/bin/search.sh" "test" --limit 1 >/dev/null 2>&1; then
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
