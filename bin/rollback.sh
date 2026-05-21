#!/bin/bash
# AI Memory System v1 — Emergency Rollback
# Restores pre-memory-system state in < 5 seconds
set -e

echo "Rolling back memory system..."

# Only restore settings.json (contains hook registrations)
if [ -f ~/.claude/settings.json.pre-memory-system ]; then
    cp ~/.claude/settings.json.pre-memory-system ~/.claude/settings.json
    echo "  Restored settings.json (removes hook registrations)"
else
    echo "  WARNING: No settings.json backup. Remove hook entries manually."
fi

# Remove memory-system hooks only (not other hooks' backups)
rm -f ~/.claude/hooks/smart-memory-inject.sh
rm -f ~/.claude/hooks/session-signals.sh

# Remove auto-generated context
rm -f ~/.claude/rules/memory-context.md

echo ""
echo "DONE. Memory system deactivated."
echo "Infra stays at ~/.claude/memory-system/ (harmless). Delete manually if wanted."
echo "Restart Claude Code session to take effect."
