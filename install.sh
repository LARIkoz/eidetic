#!/bin/bash
# Claude Memory System — Installer
# Zero external deps: bash + python3 + sqlite3 (all pre-installed on macOS/Linux)
set -euo pipefail

MEMORY_SYSTEM="$HOME/.claude/memory-system"
HOOKS_DIR="$HOME/.claude/hooks"
SKILLS_DIR="$HOME/.claude/skills/memory-recall"
RULES_DIR="$HOME/.claude/rules"
SETTINGS="$HOME/.claude/settings.json"

echo "=== Claude Memory System — Install ==="
echo ""

# Check prerequisites
command -v python3 >/dev/null || { echo "ERROR: python3 required"; exit 1; }
command -v sqlite3 >/dev/null || { echo "ERROR: sqlite3 required"; exit 1; }
[ -d "$HOME/.claude" ] || { echo "ERROR: ~/.claude/ not found. Install Claude Code first."; exit 1; }

# Backup current state
echo "1. Creating backup..."
if [ -f "$SETTINGS" ]; then
    cp "$SETTINGS" "$SETTINGS.pre-memory-system"
    echo "   Backed up settings.json"
fi
for f in "$HOOKS_DIR"/*.sh; do
    [ -f "$f" ] && cp "$f" "$f.bak"
done
echo "   Backed up existing hooks"

# Install memory system
echo "2. Installing memory system..."
mkdir -p "$MEMORY_SYSTEM"/{bin,db}
cp bin/*.sh bin/*.py "$MEMORY_SYSTEM/bin/"
cp mcp_server.py "$MEMORY_SYSTEM/mcp_server.py"
chmod +x "$MEMORY_SYSTEM/bin/"*.sh

# Install hooks
echo "3. Installing hooks..."
mkdir -p "$HOOKS_DIR"
cp hooks/*.sh "$HOOKS_DIR/"
chmod +x "$HOOKS_DIR/smart-memory-inject.sh" "$HOOKS_DIR/session-signals.sh"

# Install skill
echo "4. Installing recall skill..."
mkdir -p "$SKILLS_DIR"
cp skill/SKILL.md "$SKILLS_DIR/"

# Create rules directory
mkdir -p "$RULES_DIR"

# Register hooks in settings.json
echo "5. Registering hooks..."
if [ -f "$SETTINGS" ]; then
    python3 << 'PYEOF'
import json, sys, os

settings_path = os.path.expanduser("~/.claude/settings.json")
with open(settings_path) as f:
    settings = json.load(f)

hooks = settings.setdefault("hooks", {})

# Add SessionStart hook (after existing ones)
session_start = hooks.setdefault("SessionStart", [])
inject_hook = {
    "hooks": [{
        "type": "command",
        "command": "~/.claude/hooks/smart-memory-inject.sh",
        "timeout": 5000
    }]
}
if not any("smart-memory-inject" in str(h) for h in session_start):
    session_start.append(inject_hook)
    print("   Added smart-memory-inject to SessionStart")
else:
    print("   smart-memory-inject already registered")

# Add Stop hook (async)
stop = hooks.setdefault("Stop", [])
signal_entry = {
    "type": "command",
    "command": "~/.claude/hooks/session-signals.sh",
    "timeout": 30000,
    "async": True
}
if not any("session-signals" in str(h) for h in stop):
    if stop and "hooks" in stop[0]:
        stop[0]["hooks"].append(signal_entry)
    else:
        stop.append({"hooks": [signal_entry]})
    print("   Added session-signals to Stop")
else:
    print("   session-signals already registered")

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
PYEOF
fi

# Build initial index
echo "6. Building FTS5 index..."
"$MEMORY_SYSTEM/bin/index.sh" --full 2>&1

# Run health check
echo ""
echo "7. Health check..."
"$MEMORY_SYSTEM/bin/health.sh"

echo ""
echo "=== Installation complete ==="
echo ""
echo "What happens now:"
echo "  - Every session start: 59+ feedback rules auto-injected"
echo "  - Every session end: signals extracted and compounded"
echo "  - Search: ~/.claude/memory-system/bin/search.sh \"your query\""
echo "  - Recall skill: /memory-recall in Claude Code"
echo "  - Health: ~/.claude/memory-system/bin/health.sh"
echo "  - Rollback: bash ~/.claude/memory-system/bin/rollback.sh"
