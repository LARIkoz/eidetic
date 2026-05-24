#!/bin/bash
# Eidetic — update to latest version
# Preserves: db/, rules/memory-context.md, settings.json hooks
set -euo pipefail

MEMORY_SYSTEM="$HOME/.claude/memory-system"
META="$MEMORY_SYSTEM/.installed.json"
REPO="https://github.com/LARIkoz/eidetic.git"
TMP_DIR=$(mktemp -d)

trap 'rm -rf "$TMP_DIR"' EXIT

echo "=== Eidetic — Update ==="

if [ -f "$META" ]; then
    OLD_VER=$(python3 -c "import json; print(json.load(open('$META')).get('version','unknown'))" 2>/dev/null || echo "unknown")
    echo "Current: v$OLD_VER"
else
    OLD_VER="unknown"
    echo "Current: not tracked"
fi

echo "Fetching latest from GitHub..."
git clone --depth 1 "$REPO" "$TMP_DIR/eidetic" 2>/dev/null

NEW_VER=$(sed -n 's/.*version-\([0-9][0-9.]*\)-.*/\1/p' "$TMP_DIR/eidetic/README.md" | head -1)
[ -z "$NEW_VER" ] && NEW_VER="unknown"
NEW_SHA=$(git -C "$TMP_DIR/eidetic" rev-parse HEAD 2>/dev/null || echo "unknown")
echo "Latest:  v$NEW_VER ($NEW_SHA)"

if [ -f "$META" ]; then
    LOCAL_SHA=$(python3 -c "import json; print(json.load(open('$META')).get('git_sha',''))" 2>/dev/null || echo "")
    if [ "$LOCAL_SHA" = "$NEW_SHA" ]; then
        echo "Already up to date."
        rm -f "$MEMORY_SYSTEM/.update-available"
        exit 0
    fi
fi

echo ""
echo "Updating bin/ and mcp_server.py (preserving db/, rules, hooks registration)..."

cp "$TMP_DIR/eidetic/bin/"*.sh "$TMP_DIR/eidetic/bin/"*.py "$MEMORY_SYSTEM/bin/"
chmod +x "$MEMORY_SYSTEM/bin/"*.sh
cp "$TMP_DIR/eidetic/mcp_server.py" "$MEMORY_SYSTEM/mcp_server.py"

if [ -d "$TMP_DIR/eidetic/hooks" ]; then
    for hook in "$TMP_DIR/eidetic/hooks/"*.sh; do
        HOOK_NAME=$(basename "$hook")
        TARGET="$HOME/.claude/hooks/$HOOK_NAME"
        if [ -f "$TARGET" ]; then
            cp "$TARGET" "$TARGET.pre-update"
        fi
        cp "$hook" "$TARGET"
        chmod +x "$TARGET"
    done
    echo "Hooks updated (pre-update backups saved)"
fi

if [ -d "$TMP_DIR/eidetic/skill" ]; then
    mkdir -p "$HOME/.claude/skills/memory-recall"
    cp "$TMP_DIR/eidetic/skill/SKILL.md" "$HOME/.claude/skills/memory-recall/"
    echo "Skill updated"
fi

echo "Refreshing derived indexes..."
"$MEMORY_SYSTEM/bin/index.sh" --incremental 2>&1 || true
if python3 -c "import tree_sitter" 2>/dev/null; then
    python3 "$MEMORY_SYSTEM/bin/code_index.py" "$MEMORY_SYSTEM/db/index.db" "$MEMORY_SYSTEM" --slug claude-memory-system 2>&1 || true
fi
if [ -f "$MEMORY_SYSTEM/db/vectors.db" ]; then
    python3 "$MEMORY_SYSTEM/bin/embed.py" "$MEMORY_SYSTEM/db/index.db" "$MEMORY_SYSTEM/db/vectors.db" 2>&1 || true
fi
python3 "$MEMORY_SYSTEM/bin/assemble_context.py" "$MEMORY_SYSTEM/db/index.db" "$HOME/.claude/rules/memory-context.md" "$(pwd)" 2>&1 || true

python3 -c "
import json, time
meta = {
    'version': '$NEW_VER',
    'git_sha': '$NEW_SHA',
    'repo': '$REPO',
    'installed_at': json.load(open('$META')).get('installed_at', '') if __import__('os').path.exists('$META') else '',
    'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'update_method': 'auto'
}
with open('$META', 'w') as f:
    json.dump(meta, f, indent=2)
"

rm -f "$MEMORY_SYSTEM/.update-available"

echo ""
echo "=== Updated to v$NEW_VER ==="
echo "Preserved: db/ (index + vectors), rules/memory-context.md, settings.json hooks"
echo "Derived indexes and memory context refreshed. Run ~/.claude/memory-system/bin/index.sh --full only if you need a full rebuild."
