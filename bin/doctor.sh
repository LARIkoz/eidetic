#!/usr/bin/env bash
# Eidetic — Doctor / self-check.
#
# Diagnoses the WHOLE chain end-to-end and says, in plain language, whether
# Eidetic can do its job — especially "can it build the wiki/vault?" — and if
# not, WHY and HOW to fix it. Zero external deps (bash + python3 + sqlite3).
#
# Exit code = number of FAIL checks (0 = healthy).
#
# Triggers this was built for:
#   - "eidetic проверка" / "eidetic doctor" / "eidetic health"
#   - friend installed it but "no wiki in projects, no folder/structure"
#   - the 16-day silent vector outage (model cache evicted from TMPDIR)
set -uo pipefail

# ---- resolve the installed memory-system root (same logic as health.sh) ----
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALLED_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -n "${EIDETIC_MEMORY_SYSTEM:-}" ]; then
    MEMORY_SYSTEM="$EIDETIC_MEMORY_SYSTEM"
elif [ -f "$INSTALLED_ROOT/.installed.json" ]; then
    MEMORY_SYSTEM="$INSTALLED_ROOT"
else
    MEMORY_SYSTEM="$HOME/.claude/memory-system"
fi
DB="$MEMORY_SYSTEM/db/index.db"
VDB="$MEMORY_SYSTEM/db/vectors.db"
SETTINGS="$HOME/.claude/settings.json"

PASS=0; WARN=0; FAIL=0
declare -a FIXES=()
ok()   { echo "  ✅ $1"; PASS=$((PASS+1)); }
warn() { echo "  ⚠️  $1"; WARN=$((WARN+1)); [ -n "${2:-}" ] && FIXES+=("$2"); }
bad()  { echo "  ❌ $1"; FAIL=$((FAIL+1)); [ -n "${2:-}" ] && FIXES+=("$2"); }
note() { echo "  ⬜ $1"; }
hdr()  { echo; echo "$1"; }

echo "=== Eidetic Doctor ==="
echo "memory-system: $MEMORY_SYSTEM"

# ---------------------------------------------------------------- DEPENDENCIES
hdr "Dependencies"
if command -v python3 >/dev/null; then ok "python3 ($(python3 -V 2>&1 | cut -d' ' -f2))"; else bad "python3 missing" "install python3"; fi
if command -v sqlite3 >/dev/null; then ok "sqlite3"; else bad "sqlite3 missing" "install sqlite3"; fi
# fastembed = the vector / cross-lingual search engine. Optional: without it
# Eidetic still runs FTS-only (keyword) search, but loses semantic + RU→EN recall.
if python3 -c "import fastembed" 2>/dev/null; then
    ok "fastembed ($(python3 -c 'import fastembed; print(fastembed.__version__)' 2>/dev/null)) — vector search available"
else
    warn "fastembed not importable — vector/semantic search OFF (FTS keyword-only)" "pip3 install fastembed"
fi

# ------------------------------------------------------------------- FTS INDEX
hdr "Index (FTS5)"
FILES=""; CHUNKS=""
if [ -f "$DB" ]; then
    CHUNKS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM memory_chunks" 2>/dev/null || echo "?")
    FILES=$(sqlite3 "$DB" "SELECT COUNT(DISTINCT path) FROM memory_chunks" 2>/dev/null || echo "?")
    if [ "${CHUNKS:-0}" = "0" ] || [ "${CHUNKS:-?}" = "?" ]; then
        warn "index.db exists but is EMPTY (0 chunks) — nothing indexed yet" "bash $MEMORY_SYSTEM/bin/index.sh --full"
    else
        ok "index.db: $FILES files, $CHUNKS chunks ($(du -h "$DB" 2>/dev/null | cut -f1))"
    fi
else
    bad "index.db missing — Eidetic has no memory index" "bash $MEMORY_SYSTEM/bin/index.sh --full"
fi

# memory files on disk — the SOURCE of the index and the wiki. Zero here is the
# #1 reason a fresh install shows "no wiki": there is simply nothing to export.
MEM_FILES=$(find "$HOME/.claude/projects" -path "*/memory/*.md" -type f 2>/dev/null | wc -l | tr -d ' ')
if [ "${MEM_FILES:-0}" -gt 0 ]; then
    ok "$MEM_FILES memory .md files on disk (~/.claude/projects/*/memory/)"
else
    warn "0 memory files found — nothing to index or turn into a wiki yet" "memories accrue as you work; or check the session-signals Stop hook is capturing"
fi

# ------------------------------------------------------------ VECTORS / MODEL
hdr "Vectors & embedding model"
if [ -f "$VDB" ]; then
    VCOUNT=$(sqlite3 "$VDB" "SELECT COUNT(*) FROM vectors" 2>/dev/null || echo "?")
    VMODEL=$(sqlite3 "$VDB" "SELECT value FROM meta WHERE key='model'" 2>/dev/null || echo "")
    [ -n "$VMODEL" ] && ok "vectors.db: $VCOUNT vectors, model=$VMODEL" || warn "vectors.db: $VCOUNT vectors, NO model stamp" "rebuild: bash $MEMORY_SYSTEM/bin/index.sh --full"
    # vector lag: vectors should roughly track indexed chunks (code chunks count too)
    if [ "${CHUNKS:-0}" != "0" ] && [ "${VCOUNT:-?}" != "?" ] && [ "${CHUNKS:-?}" != "?" ]; then
        LAGPCT=$(( (CHUNKS - VCOUNT) * 100 / CHUNKS ))
        if [ "$LAGPCT" -gt 20 ]; then
            EMBED_RUNNING=$(pgrep -f "bin/embed.py" >/dev/null && echo " (embed running now — catching up)" || echo "")
            warn "vector lag ${LAGPCT}% ($VCOUNT/$CHUNKS) — recent memories not semantically searchable${EMBED_RUNNING}" "bash $MEMORY_SYSTEM/bin/index.sh --incremental  # or embed.py"
        else
            ok "vector coverage healthy ($VCOUNT/$CHUNKS, lag ${LAGPCT}%)"
        fi
    fi
else
    note "vectors.db not built yet (vector search inactive; FTS still works)"
fi
# model cache location — TMPDIR caches get purged by macOS, silently evicting the
# ~2GB weights and breaking all vector search until reindex (the 16-day outage).
PERSIST_CACHE="${FASTEMBED_CACHE_PATH:-$HOME/.cache/fastembed}"
if [ -d "$PERSIST_CACHE" ] && find "$PERSIST_CACHE" -iname "*e5-large*" -maxdepth 2 >/dev/null 2>&1 && [ -n "$(find "$PERSIST_CACHE" -iname '*e5-large*' -maxdepth 2 2>/dev/null)" ]; then
    ok "model cache persistent: $PERSIST_CACHE"
else
    TMP_CACHE=$(find "${TMPDIR:-/tmp}" -maxdepth 2 -iname "fastembed_cache" -type d 2>/dev/null | head -1)
    if [ -n "$TMP_CACHE" ]; then
        warn "model cache lives in TMPDIR ($TMP_CACHE) — macOS will purge it and break vector search" "set FASTEMBED_CACHE_PATH=$PERSIST_CACHE and reindex (fixed in embed.py>=cache-pin)"
    else
        note "embedding model not downloaded yet (downloads on first vector index)"
    fi
fi

# --------------------------------------------------------------------- HOOKS
hdr "Hooks & automation (settings.json)"
hookchk() { if grep -q "$1" "$SETTINGS" 2>/dev/null; then ok "$2 hook installed"; else warn "$2 hook NOT installed" "re-run install.sh"; fi; }
hookchk "smart-memory-inject" "session-start inject"
hookchk "session-signals"     "memory-capture (signals)"
# the export-vault hook is what actually BUILDS the wiki on every session stop —
# its absence is a prime suspect for "no wiki".
if grep -q "export-vault" "$SETTINGS" 2>/dev/null; then ok "export-vault (wiki build) hook installed"; else warn "export-vault hook NOT installed — wiki is never auto-built" "add a Stop hook running bin/export-vault.sh, or run it manually"; fi
if crontab -l 2>/dev/null | grep -q "export-vault"; then ok "export-vault cron present"; else note "no export-vault cron (optional nightly rebuild)"; fi

# ------------------------------------------------------------- WIKI / VAULT
hdr "Wiki / vault (Obsidian export)"
EXPORT_SH="$MEMORY_SYSTEM/bin/export-vault.sh"
[ -x "$EXPORT_SH" ] && ok "export-vault.sh present + executable" || bad "export-vault.sh missing/not executable" "re-run install.sh"
# figure out WHERE the vault should be: the hook/cron target, else the default.
VAULT=$(grep -oE "export-vault.sh +[^ ]+/[^ ]*eidetic-vault" "$SETTINGS" 2>/dev/null | grep -oE "/[^ ]*eidetic-vault" | head -1)
[ -z "$VAULT" ] && VAULT=$(crontab -l 2>/dev/null | grep -oE "/[^ ]*eidetic-vault" | head -1)
[ -z "$VAULT" ] && VAULT="$HOME/Documents/eidetic-vault"
if [ -d "$VAULT" ]; then
    PAGES=$(find "$VAULT" -name "*.md" -type f 2>/dev/null | wc -l | tr -d ' ')
    NEWEST=$(find "$VAULT" -name "*.md" -type f -exec stat -f '%m' {} \; 2>/dev/null | sort -rn | head -1)
    if [ "${PAGES:-0}" -gt 0 ]; then
        AGE_NOTE=""
        ok "vault exists: $VAULT ($PAGES pages)"
        [ -f "$VAULT/HOME.md" ] && ok "vault HOME.md hub present" || warn "vault has no HOME.md hub" "re-run export-vault"
    else
        bad "vault dir exists but is EMPTY ($VAULT)" "bash $EXPORT_SH"
    fi
else
    bad "NO vault/wiki at $VAULT — never generated" "bash $EXPORT_SH   (after you have memories)"
fi

# explicit "why no wiki?" diagnosis (the friend's exact symptom)
if [ ! -d "$VAULT" ] || [ "${PAGES:-0}" = "0" ]; then
    echo
    echo "  → Why no wiki? Most likely cause, in order:"
    [ "${MEM_FILES:-0}" = "0" ] && echo "     1. You have 0 memory files yet — nothing to export. Use Claude Code; memories accrue."
    grep -q "export-vault" "$SETTINGS" 2>/dev/null || echo "     2. The export-vault hook isn't installed — the wiki is never auto-built."
    [ -x "$EXPORT_SH" ] && echo "     3. It may have never run — try once manually: bash $EXPORT_SH"
fi

# ------------------------------------------------------------------- SEARCH
hdr "Search"
SEARCH_BIN="$MEMORY_SYSTEM/bin/search.sh"
[ -x "$SEARCH_BIN" ] || SEARCH_BIN="$SCRIPT_DIR/search.sh"
if "$SEARCH_BIN" "test" --limit 1 >/dev/null 2>&1; then ok "search runs"; else bad "search broken" "check index.db + bin/search_impl.py"; fi

# ------------------------------------------------------------------ SUMMARY
hdr "Summary"
echo "  PASS=$PASS  WARN=$WARN  FAIL=$FAIL"
if [ "$FAIL" -gt 0 ]; then echo "  VERDICT: ❌ broken — $FAIL blocking issue(s)";
elif [ "$WARN" -gt 0 ]; then echo "  VERDICT: ⚠️  degraded — works, but $WARN thing(s) to fix";
else echo "  VERDICT: ✅ healthy"; fi
if [ "${#FIXES[@]}" -gt 0 ]; then
    echo; echo "  Suggested fixes:"
    for f in "${FIXES[@]}"; do echo "    • $f"; done
fi
exit "$FAIL"
