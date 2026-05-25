#!/bin/bash
# Eidetic — update to latest version
# Preserves: db/, rules/memory-context.md, settings.json hooks
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
META="$MEMORY_SYSTEM/.installed.json"
REPO="https://github.com/LARIkoz/eidetic.git"
TMP_DIR=$(mktemp -d)

trap 'rm -rf "$TMP_DIR"' EXIT

echo "=== Eidetic — Update ==="

if [ -f "$META" ]; then
    OLD_VER=$(
python3 - "$META" 2>/dev/null << 'PYEOF' || echo "unknown"
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    print(json.load(f).get("version", "unknown"))
PYEOF
)
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
    LOCAL_SHA=$(
python3 - "$META" 2>/dev/null << 'PYEOF' || echo ""
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    print(json.load(f).get("git_sha", ""))
PYEOF
)
    if [ "$LOCAL_SHA" = "$NEW_SHA" ]; then
        echo "Already up to date."
        rm -f "$MEMORY_SYSTEM/.update-available"
        exit 0
    fi
fi

echo ""
echo "Updating bin/ and mcp_server.py (preserving db/, rules, hooks registration)..."

atomic_install() {
    src="$1"
    dst="$2"
    mode="${3:-}"
    mkdir -p "$(dirname "$dst")"
    tmp=$(mktemp "${dst}.tmp.XXXXXX")
    if ! cp "$src" "$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    if [ -n "$mode" ]; then
        chmod "$mode" "$tmp"
    fi
    if ! mv -f "$tmp" "$dst"; then
        rm -f "$tmp"
        return 1
    fi
}

for src in "$TMP_DIR/eidetic/bin/"*.sh; do
    atomic_install "$src" "$MEMORY_SYSTEM/bin/$(basename "$src")" 755
done
for src in "$TMP_DIR/eidetic/bin/"*.py; do
    mode=644
    [ -x "$src" ] && mode=755
    atomic_install "$src" "$MEMORY_SYSTEM/bin/$(basename "$src")" "$mode"
done
atomic_install "$TMP_DIR/eidetic/mcp_server.py" "$MEMORY_SYSTEM/mcp_server.py" 644

if [ -d "$TMP_DIR/eidetic/hooks" ]; then
    for hook in "$TMP_DIR/eidetic/hooks/"*.sh; do
        HOOK_NAME=$(basename "$hook")
        TARGET="$HOME/.claude/hooks/$HOOK_NAME"
        if [ -f "$TARGET" ]; then
            cp "$TARGET" "$TARGET.pre-update"
        fi
        atomic_install "$hook" "$TARGET" 755
    done
    echo "Hooks updated (pre-update backups saved)"
fi

SETTINGS="$HOME/.claude/settings.json"
if [ -f "$SETTINGS" ]; then
    EIDETIC_INSTALL_MEMORY_SYSTEM="$MEMORY_SYSTEM" python3 << 'PYEOF'
import json, os, shlex, tempfile

settings_path = os.path.expanduser("~/.claude/settings.json")
with open(settings_path, encoding="utf-8") as f:
    settings = json.load(f)

hooks = settings.setdefault("hooks", {})
memory_system = os.environ.get("EIDETIC_INSTALL_MEMORY_SYSTEM", "")
default_memory_system = os.path.expanduser("~/.claude/memory-system")
hook_prefix = ""
if memory_system and os.path.abspath(os.path.expanduser(memory_system)) != os.path.abspath(default_memory_system):
    hook_prefix = "EIDETIC_MEMORY_SYSTEM={} ".format(shlex.quote(memory_system))

session_start = hooks.setdefault("SessionStart", [])
inject_hook = {
    "type": "command",
    "command": hook_prefix + "~/.claude/hooks/smart-memory-inject.sh",
    "timeout": 5000,
}
inject_updated = False
for entry in session_start:
    for hook in entry.get("hooks", []) if isinstance(entry, dict) else []:
        if "smart-memory-inject" in str(hook.get("command", "")):
            hook.update(inject_hook)
            inject_updated = True
if not inject_updated:
    session_start.append({"hooks": [inject_hook]})

stop = hooks.setdefault("Stop", [])
signal_entry = {
    "type": "command",
    "command": hook_prefix + "~/.claude/hooks/session-signals.sh",
    "timeout": 180000,
    "async": True,
}
signal_updated = False
for entry in stop:
    for hook in entry.get("hooks", []) if isinstance(entry, dict) else []:
        if "session-signals" in str(hook.get("command", "")):
            hook.update(signal_entry)
            signal_updated = True
if not signal_updated:
    if stop and isinstance(stop[0], dict) and "hooks" in stop[0]:
        stop[0]["hooks"].append(signal_entry)
    else:
        stop.append({"hooks": [signal_entry]})

settings_dir = os.path.dirname(settings_path) or "."
fd, tmp = tempfile.mkstemp(dir=settings_dir, prefix=os.path.basename(settings_path) + ".tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(settings, f, indent=2)
os.replace(tmp, settings_path)
PYEOF
    echo "Hook routing updated"
fi

if [ -d "$TMP_DIR/eidetic/skill" ]; then
    mkdir -p "$HOME/.claude/skills/memory-recall"
    atomic_install "$TMP_DIR/eidetic/skill/SKILL.md" "$HOME/.claude/skills/memory-recall/SKILL.md" 644
    if [ "$MEMORY_SYSTEM" != "$HOME/.claude/memory-system" ]; then
        python3 - "$HOME/.claude/skills/memory-recall/SKILL.md" "$MEMORY_SYSTEM" << 'PYEOF'
import os, pathlib, shlex, sys, tempfile

path = pathlib.Path(sys.argv[1])
memory_system = sys.argv[2]
text = path.read_text(encoding="utf-8")
fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(text.replace("~/.claude/memory-system", shlex.quote(memory_system)))
os.replace(tmp, path)
PYEOF
    fi
    echo "Skill updated"
fi

echo "Refreshing derived indexes..."
REFRESH_FAILED=0
run_refresh_step() {
    local label="$1"
    shift
    if "$@" 2>&1; then
        return 0
    fi
    local rc=$?
    echo "WARNING: refresh step failed ($label, exit $rc)"
    REFRESH_FAILED=1
    return 0
}

run_refresh_step "fts-index" "$MEMORY_SYSTEM/bin/index.sh" --incremental
if python3 -c "import tree_sitter" 2>/dev/null; then
    run_refresh_step "code-index" python3 "$MEMORY_SYSTEM/bin/code_index.py" "$MEMORY_SYSTEM/db/index.db" "$MEMORY_SYSTEM" --slug claude-memory-system
fi
if [ -f "$MEMORY_SYSTEM/db/vectors.db" ]; then
    run_refresh_step "vectors" python3 "$MEMORY_SYSTEM/bin/embed.py" "$MEMORY_SYSTEM/db/index.db" "$MEMORY_SYSTEM/db/vectors.db"
fi
run_refresh_step "memory-context" python3 "$MEMORY_SYSTEM/bin/assemble_context.py" "$MEMORY_SYSTEM/db/index.db" "$HOME/.claude/rules/memory-context.md" "$(pwd)"

python3 - "$META" "$NEW_VER" "$NEW_SHA" "$REPO" << 'PYEOF'
import json, os, sys, tempfile, time

meta_path, new_ver, new_sha, repo = sys.argv[1:5]
installed_at = ""
if os.path.exists(meta_path):
    with open(meta_path, encoding="utf-8") as f:
        installed_at = json.load(f).get("installed_at", "")
meta = {
    "version": new_ver,
    "git_sha": new_sha,
    "repo": repo,
    "installed_at": installed_at,
    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "update_method": "auto",
}
os.makedirs(os.path.dirname(meta_path), exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(meta_path), prefix=os.path.basename(meta_path) + ".tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(meta, f, indent=2)
os.replace(tmp, meta_path)
PYEOF

rm -f "$MEMORY_SYSTEM/.update-available"

echo ""
echo "=== Updated to v$NEW_VER ==="
echo "Preserved: db/ (index + vectors), rules/memory-context.md, settings.json hooks"
COMMAND_MEMORY_SYSTEM=$(printf '%q' "$MEMORY_SYSTEM")
if [ "$REFRESH_FAILED" -eq 0 ]; then
    echo "Derived indexes and memory context refreshed. Run $COMMAND_MEMORY_SYSTEM/bin/index.sh --full only if you need a full rebuild."
else
    echo "WARNING: Runtime files updated, but one or more derived refresh steps failed."
    echo "Run $COMMAND_MEMORY_SYSTEM/bin/health.sh and refresh the failing derived artifact before trusting recall."
    exit 2
fi
