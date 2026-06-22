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
CAUDIT="$SCRIPT_DIR/coverage_audit.py"   # guard-accurate vector-alignment truth (replaces gross lag)
SETTINGS="$HOME/.claude/settings.json"

# Topic-base mode: pointed at a base (a .eidetic-base.json at the root) the PUSH-only
# machinery — session hooks, Obsidian wiki export, auto-compound/op-log, the
# ~/.claude/projects file count, session-end signal extraction — is N/A by design.
# Checking it against a base root yields FALSE ❌/⚠️ (a base has none of it). Gate those
# sections so a base reads true; the index/vectors/canary/translation/search/usage
# checks that DO apply to a base still run.
BASE_MODE=0; BASE_NAME=""
if [ -f "$MEMORY_SYSTEM/.eidetic-base.json" ]; then
    BASE_MODE=1
    BASE_NAME=$(python3 -c "import json; print(json.load(open('$MEMORY_SYSTEM/.eidetic-base.json')).get('name',''))" 2>/dev/null)
fi

# --brief: one-line health snapshot for handoffs / status lines (no full report).
if [ "${1:-}" = "--brief" ]; then
    DDB="$MEMORY_SYSTEM/db/drift_state.db"
    fc=$(sqlite3 "$DB" "SELECT COUNT(DISTINCT path) FROM memory_chunks" 2>/dev/null)
    cc=$(sqlite3 "$DB" "SELECT COUNT(*) FROM memory_chunks" 2>/dev/null)
    vc=$(sqlite3 "$VDB" "SELECT COUNT(*) FROM vectors" 2>/dev/null)
    md=$(sqlite3 "$VDB" "SELECT value FROM meta WHERE key='model'" 2>/dev/null)
    dr=$(sqlite3 "$DDB" "SELECT COUNT(*) FROM drift_findings WHERE resolved_at IS NULL" 2>/dev/null)
    mem=$(find "$HOME/.claude/projects" -path "*/memory/*.md" -type f 2>/dev/null | wc -l | tr -d ' ')
    # Vector health = ALIGNED coverage (guard-accurate), NOT the gross
    # (chunks-vectors)/chunks lag — that counted dead orphan-vectors as coverage
    # and hid the 99.94% chunk_id-misalignment outage as "lag -319% / healthy".
    # coverage_audit.py is the single truth source; bare count is the fallback.
    align_pct=""; orphan=""; blind_files=""
    [ -f "$CAUDIT" ] && { eval "$(python3 "$CAUDIT" "$DB" "$VDB" --oneline 2>/dev/null)" 2>/dev/null || true; }
    if [ -n "${align_pct:-}" ]; then
        echo "Eidetic memory: ${fc:-?} files / ${cc:-?} chunks / ${vc:-?} vectors (${md##*/}, ${align_pct}% aligned, ${orphan} orphan, ${blind_files} blind) · ${mem} memory .md on disk · ${dr:-0} open drift findings"
    else
        echo "Eidetic memory: ${fc:-?} files / ${cc:-?} chunks / ${vc:-?} vectors (${md##*/}, coverage unknown) · ${mem} memory .md on disk · ${dr:-0} open drift findings"
    fi
    exit 0
fi

PASS=0; WARN=0; FAIL=0
declare -a FIXES=()
ok()   { echo "  ✅ $1"; PASS=$((PASS+1)); }
warn() { echo "  ⚠️  $1"; WARN=$((WARN+1)); [ -n "${2:-}" ] && FIXES+=("$2"); }
bad()  { echo "  ❌ $1"; FAIL=$((FAIL+1)); [ -n "${2:-}" ] && FIXES+=("$2"); }
note() { echo "  ⬜ $1"; }
hdr()  { echo; echo "$1"; }

echo "=== Eidetic Doctor ==="
echo "memory-system: $MEMORY_SYSTEM"
[ "$BASE_MODE" = 1 ] && echo "mode: topic base${BASE_NAME:+ '$BASE_NAME'} (PULL — search/translation checked; PUSH machinery N/A)"

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
if [ "$BASE_MODE" = 0 ]; then
MEM_FILES=$(find "$HOME/.claude/projects" -path "*/memory/*.md" -type f 2>/dev/null | wc -l | tr -d ' ')
if [ "${MEM_FILES:-0}" -gt 0 ]; then
    ok "$MEM_FILES memory .md files on disk (~/.claude/projects/*/memory/)"
else
    warn "0 memory files found — nothing to index or turn into a wiki yet" "memories accrue as you work; or check the session-signals Stop hook is capturing"
fi
fi

# §3.5 (informational) — index freshness: of the files the indexer's OWN collect_files()
# says are in scope, how many are MISSING from the FTS index. Calling collect_files()
# directly = ZERO scope-drift: it covers ALL roots (projects/*/memory[/signals],
# agent-memory, memory-system/signals, skills/*/SKILL.md; excl MEMORY.md/BACKLOG.md/.bak).
# A bash glob reimplementation either over-counts (MEMORY.md/handoff-subdirs → permanent
# false "behind" — the v5.8.1 bug) or, scoped to projects/memory only, is BLIND to lag in
# agent-memory/skills/signals (177 paths the old query never saw). Set-compare on the
# indexer's exact path strings: a fresh index reads Δ0; a real incremental-hook lag shows.
if [ -f "$DB" ]; then
    FRESH=$(python3 -c "
import sys; sys.path.insert(0,'$SCRIPT_DIR')
import index_impl, sqlite3, os
ms = index_impl.memory_system_from_db('$DB') if hasattr(index_impl,'memory_system_from_db') else os.path.expanduser('~/.claude/memory-system')
disk = index_impl.collect_files(ms)
conn = sqlite3.connect('file:$DB?mode=ro', uri=True)
idx = set(r[0] for r in conn.execute('SELECT DISTINCT path FROM memory_chunks'))
print('{}|{}'.format(len(disk), sum(1 for p in disk if p not in idx)))
" 2>/dev/null)
    SCOPE_FILES=${FRESH%%|*}; LAG=${FRESH##*|}
    if [ -n "$FRESH" ] && [ "${SCOPE_FILES:-0}" -gt 0 ] 2>/dev/null && [ "${LAG:-x}" -ge 0 ] 2>/dev/null; then
        IDX_MEM=$(( SCOPE_FILES - LAG ))
        THRESH=$(( SCOPE_FILES / 10 )); [ "$THRESH" -lt 20 ] && THRESH=20   # 10% or 20 files, whichever is larger
        if [ "$LAG" -gt "$THRESH" ]; then
            note "index vs disk: $IDX_MEM indexed / $SCOPE_FILES in-scope files (Δ$LAG missing from FTS — incremental index may be lagging). Catch up: bash $MEMORY_SYSTEM/bin/index.sh --incremental"
        else
            ok "index fresh vs disk: $IDX_MEM / $SCOPE_FILES in-scope files in FTS (Δ$LAG; $([ "$BASE_MODE" = 1 ] && echo "base corpus_dirs" || echo "covers projects+agent-memory+skills+signals"))"
        fi
    fi
fi

# ------------------------------------------------------------ VECTORS / MODEL
hdr "Vectors & embedding model"
if [ -f "$VDB" ]; then
    VCOUNT=$(sqlite3 "$VDB" "SELECT COUNT(*) FROM vectors" 2>/dev/null || echo "?")
    VMODEL=$(sqlite3 "$VDB" "SELECT value FROM meta WHERE key='model'" 2>/dev/null || echo "")
    [ -n "$VMODEL" ] && ok "vectors.db: $VCOUNT vectors, model=$VMODEL" || warn "vectors.db: $VCOUNT vectors, NO model stamp" "rebuild: bash $MEMORY_SYSTEM/bin/index.sh --full"
    # Vector REAL coverage = chunks whose vector the search guard would ACCEPT
    # (join by chunk_id + path/heading/content_hash all match). The old
    # (CHUNKS-VCOUNT)/CHUNKS lag counted dead orphan-vectors as coverage and
    # rendered the 99.94% misalignment outage as "healthy, lag -319%". This gate
    # reads coverage_audit.py (guard-accurate) and FAILS when vectors exist but
    # are chunk_id-misaligned — the exact silent outage the gross count hid.
    if [ -f "$CAUDIT" ]; then
        align_pct=""; aligned=""; total=""; orphan=""; blind_files=""; no_vector=""
        eval "$(python3 "$CAUDIT" "$DB" "$VDB" --oneline 2>/dev/null)" 2>/dev/null || true
        EMBED_RUNNING=$(pgrep -f "bin/embed.py" >/dev/null && echo " (embed running now — catching up)" || echo "")
        if [ -z "${align_pct:-}" ]; then
            warn "could not compute vector alignment (coverage_audit failed)" "python3 $CAUDIT   # run manually to see the error"
        elif [ "$align_pct" -lt 80 ]; then
            bad "vectors ${align_pct}% ALIGNED ($aligned/$total) — ${orphan} dead orphan-vectors, ${blind_files} blind files: vectors EXIST but are chunk_id-misaligned, so semantic search is BLIND (a gross vector count hides this)${EMBED_RUNNING}" "bash $MEMORY_SYSTEM/bin/index.sh --full   # full rebuild realigns chunk_ids"
        elif [ "$align_pct" -lt 95 ]; then
            warn "vector alignment ${align_pct}% ($aligned/$total, ${orphan} orphan, ${blind_files} blind, ${no_vector} unembedded) — some memories not semantically searchable${EMBED_RUNNING}" "bash $MEMORY_SYSTEM/bin/index.sh --incremental"
        else
            ok "vector coverage healthy — ${align_pct}% aligned ($aligned/$total), ${orphan} orphan-vectors (harmless cruft)"
        fi
    elif [ "${CHUNKS:-0}" != "0" ] && [ "${VCOUNT:-?}" != "?" ] && [ "${CHUNKS:-?}" != "?" ]; then
        note "coverage_audit.py not present — raw count only ($VCOUNT vectors / $CHUNKS chunks); deploy coverage_audit.py for guard-accurate alignment"
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
# Cross-encoder reranker (jina) — the salvage signal for ambiguous cross-lingual
# matches. It can go missing independently of e5 (an emptied onnx/ dir → fastembed
# believes it is cached but the file is gone), silently disabling rerank salvage and
# degrading cross-lingual recall while everything else looks healthy.
if python3 -c "import fastembed" 2>/dev/null; then
    JINA_ONNX=$(find "$PERSIST_CACHE" -path "*jina-reranker*" -name "model.onnx" 2>/dev/null | head -1)
    if [ -n "$JINA_ONNX" ]; then
        ok "cross-encoder reranker present (jina) — cross-lingual salvage active"
    else
        warn "cross-encoder reranker model MISSING — rerank salvage off, cross-lingual recall degraded" "it re-downloads on the next vector query; if it persists, remove the jina-reranker dir under ~/.cache/fastembed and run a search"
    fi
fi

# W5 loud self-heal: a session-start embed crash is now recorded here instead of
# vanishing into /dev/null (that swallow hid the 16-day outage). A non-empty log
# means the LAST incremental embed failed — surface it loudly.
EMBED_LOG="$MEMORY_SYSTEM/embed-last.log"
if [ -s "$EMBED_LOG" ]; then
    warn "last session embed FAILED: $(tail -n1 "$EMBED_LOG" 2>/dev/null | cut -c1-120)" "see $EMBED_LOG, then: bash $MEMORY_SYSTEM/bin/index.sh --full"
else
    ok "no embed errors logged (W5 self-heal clean)"
fi

# ------------------------------------------------- FUNCTIONAL CANARY (embed→search)
# Everything above is STRUCTURAL (counts, file-existence, chunk_id alignment) — all
# of it passes even when the embedder is silently broken (wrong model, pooling drift,
# evicted cache) or the usage logger never fires. The canary EXERCISES the chain:
# embed a real card's name → vector search → assert it self-retrieves at rank ≤3, and
# confirm the usage logger fired (into a TEMP log, never prod). bin/canary.py. The
# §3.2 usage verdict is rendered later in the Usage section.
hdr "Functional canary (live embed → vector → search)"
CANARY_EMBED_STATUS=""; CANARY_EMBED_DETAIL=""; CANARY_USAGE_STATUS=""; CANARY_USAGE_DETAIL=""; CANARY_TRANSLATE_STATUS=""; CANARY_TRANSLATE_DETAIL=""
if [ -f "$SCRIPT_DIR/canary.py" ] && [ -f "$DB" ]; then
    CANARY_OUT=$(python3 "$SCRIPT_DIR/canary.py" --index "$DB" --vectors "$VDB" --db "$DB" 2>/dev/null)
    eval "$CANARY_OUT" 2>/dev/null || true
    case "${CANARY_EMBED_STATUS:-}" in
        ok)   ok "embed→vector→search: $CANARY_EMBED_DETAIL" ;;
        warn) warn "embed→vector→search degraded: $CANARY_EMBED_DETAIL" "bash $MEMORY_SYSTEM/bin/index.sh --full   # rebuild vectors under the active model" ;;
        fail) bad "embed→vector→search BROKEN: $CANARY_EMBED_DETAIL" "bash $MEMORY_SYSTEM/bin/index.sh --full   # then check bin/embed.py model + ~/.cache/fastembed" ;;
        skip) note "embed canary skipped: $CANARY_EMBED_DETAIL" ;;
        *)    note "embed canary did not run (canary.py error or no output)" ;;
    esac
else
    note "functional canary unavailable (canary.py or index.db missing)"
fi

# ----------------------------------------------------------- MODELS / ROUTING
# Which model does which job — so "who embeds / who writes cards / who would
# translate" is never a mystery. Embedding is the active profile; card extraction
# is the session-end LLM; cross-lingual query translation is not wired yet.
hdr "Models — who does what"
EMBED_INFO=$(python3 -c "import sys; sys.path.insert(0,'$SCRIPT_DIR'); import embed; print(f'{embed.EMBED_PROFILE}|{embed.MODEL_NAME}|{embed.VECTOR_DIM}')" 2>/dev/null)
if [ -n "$EMBED_INFO" ]; then
    EP="${EMBED_INFO%%|*}"; _rest="${EMBED_INFO#*|}"; EM="${_rest%%|*}"; ED="${_rest##*|}"
    note "Embedding (search/recall): $EM  [profile: $EP, ${ED}d]"
    # vectors.db was BUILT by VMODEL (stamp) — flag a profile↔vectors mismatch.
    if [ -n "${VMODEL:-}" ] && [ "$VMODEL" != "$EM" ]; then
        warn "active embed profile uses $EM but vectors.db was built by $VMODEL" "bash $MEMORY_SYSTEM/bin/index.sh --full   # rebuild under the active profile"
    fi
else
    note "Embedding: could not resolve the active profile (embed.py import failed)"
fi
# Card extraction: the LLM that reads the transcript tail at session end and writes
# agent-extracted memories. Defaults to Sonnet for quality (Haiku to economize).
if [ "$BASE_MODE" = 1 ]; then
    note "Card extraction (session-end signals): N/A for a topic base (no session-end capture)"
else
SIGNAL_DESC=$(EIDETIC_MEMORY_SYSTEM="$MEMORY_SYSTEM" python3 "$SCRIPT_DIR/signal_model.py" --describe 2>/dev/null)
[ -z "$SIGNAL_DESC" ] && SIGNAL_DESC="${EIDETIC_SIGNAL_CLAUDE_MODEL:-sonnet (default)}"
note "Card extraction (session-end signals): $SIGNAL_DESC  (install choice .signal_model; runtime override EIDETIC_SIGNAL_CLAUDE_MODEL; codex fallback EIDETIC_SIGNAL_CODEX_MODEL)"
fi
# Cross-lingual query translation: WIRED (opt-in, OFF by default). Show the
# configured backend, which concrete backend resolves, and per-backend availability.
# A non-English query is translated to English and dual-queried (native + translated,
# min-rank fused) — measured 5/8 -> 7/8 recall@3 (bin/recall_lab.py --translate).
# Resolve the corpus/configured source language so the Apple pack check + label aren't
# hardcoded to Russian: EIDETIC_TRANSLATE_LANG > .translate_lang > corpus auto-detect > ru.
TR_LANG=$(python3 -c "import sys; sys.path.insert(0,'$SCRIPT_DIR'); import canary; print(canary._resolve_translate_lang('$DB') or 'ru')" 2>/dev/null)
[ -z "$TR_LANG" ] && TR_LANG=ru
TR_INFO=$(python3 -c "import sys; sys.path.insert(0,'$SCRIPT_DIR'); import translate; s=translate.backend_status(source='$TR_LANG'); print('|'.join([s['configured'],str(s['resolved']),'Y' if s['apple'] else 'n','Y' if s['opusmt'] else 'n','Y' if s['cli'] else 'n']))" 2>/dev/null)
if [ -n "$TR_INFO" ]; then
    TR_CFG="${TR_INFO%%|*}"; _r="${TR_INFO#*|}"
    TR_RES="${_r%%|*}"; _r="${_r#*|}"
    TR_A="${_r%%|*}"; _r="${_r#*|}"
    TR_O="${_r%%|*}"; TR_C="${_r##*|}"
    AVAIL="apple=$TR_A opusmt=$TR_O cli=$TR_C"
    if [ "$TR_CFG" = "off" ]; then
        note "Query translation (cross-lingual): OFF — opt-in via EIDETIC_QUERY_TRANSLATE / .translate_backend  [available: $AVAIL]"
    elif [ "$TR_RES" = "None" ]; then
        warn "Query translation: '$TR_CFG' set but NO backend available  [available: $AVAIL]" "set EIDETIC_QUERY_TRANSLATE=off, or install/enable a backend"
    else
        note "Query translation (cross-lingual): $TR_CFG -> $TR_RES  [available: $AVAIL]"
    fi
    # §3.3 — explicit Apple <lang>→en pack status (replaces the implicit apple=Y/n) when
    # apple is actually in play. <lang> is the resolved corpus/configured source language
    # (not hardcoded ru). The pack is a macOS system asset downloaded once via System
    # Settings; it cannot be scripted, so the doctor must name the pair.
    if [ "$TR_CFG" = "apple" ] || [ "$TR_RES" = "apple" ]; then
        if [ "$TR_A" = "Y" ]; then
            ok "Apple translation pack ${TR_LANG}→en: installed ✓"
        else
            warn "Apple translation pack ${TR_LANG}→en: NOT installed — apple backend falls back to opusmt/cli/native" "download the '${TR_LANG}' language: System Settings → General → Translation Languages (one-time, ~tens of MB)"
        fi
    fi
    # §3.6 — FUNCTIONALLY test the translator (not just availability): the canary
    # translated a fixed RU probe; assert it came back as changed, non-Cyrillic English.
    # off/skip = translation OFF (default) or no backend — the lines above already say so.
    case "${CANARY_TRANSLATE_STATUS:-}" in
        ok)   ok "translator works: $CANARY_TRANSLATE_DETAIL" ;;
        warn) warn "translator degraded: $CANARY_TRANSLATE_DETAIL" "check the configured backend / its model" ;;
        fail) bad "translator BROKEN: $CANARY_TRANSLATE_DETAIL" "set EIDETIC_QUERY_TRANSLATE=off, or fix/reinstall the backend" ;;
    esac
else
    note "Query translation: could not resolve (translate.py import failed)"
fi

# --------------------------------------------------------------------- HOOKS
hdr "Hooks & automation (settings.json)"
if [ "$BASE_MODE" = 1 ]; then
    note "N/A for a topic base (PULL) — no session-start inject / signal-capture / wiki-export hooks"
else
hookchk() { if grep -q "$1" "$SETTINGS" 2>/dev/null; then ok "$2 hook installed"; else warn "$2 hook NOT installed" "re-run install.sh"; fi; }
hookchk "smart-memory-inject" "session-start inject"
hookchk "session-signals"     "memory-capture (signals)"
# the export-vault hook is what actually BUILDS the wiki on every session stop —
# its absence is a prime suspect for "no wiki".
if grep -q "export-vault" "$SETTINGS" 2>/dev/null; then ok "export-vault (wiki build) hook installed"; else warn "export-vault hook NOT installed — wiki is never auto-built" "add a Stop hook running bin/export-vault.sh, or run it manually"; fi
if crontab -l 2>/dev/null | grep -q "export-vault"; then ok "export-vault cron present"; else note "no export-vault cron (optional nightly rebuild)"; fi
fi

# ------------------------------------------------------------- WIKI / VAULT
hdr "Wiki / vault (Obsidian export)"
if [ "$BASE_MODE" = 1 ]; then
    note "N/A for a topic base (PULL) — a base is queried over MCP, not exported to an Obsidian wiki"
else
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
        ok "wiki/cards live: $VAULT ($PAGES pages)"
        [ -f "$VAULT/HOME.md" ] && ok "HOME.md hub present" || warn "vault has no HOME.md hub" "re-run export-vault"
        echo "     → view in Obsidian: 'Open folder as vault' → $VAULT  (start at HOME.md)"
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
fi

# ------------------------------------------------------------------- SEARCH
hdr "Search"
SEARCH_BIN="$MEMORY_SYSTEM/bin/search.sh"
[ -x "$SEARCH_BIN" ] || SEARCH_BIN="$SCRIPT_DIR/search.sh"
if "$SEARCH_BIN" "test" --limit 1 >/dev/null 2>&1; then ok "search runs"; else bad "search broken" "check index.db + bin/search_impl.py"; fi

# -------------------------------------------- COMPOUNDING & OP-LOG (Wave 2)
hdr "Compounding & op-log"
# Write-side primitives: compounding (Stop-hook signals), promote (a synthesized
# answer -> one typed page), and the op-log. Missing here = not deployed.
if [ "$BASE_MODE" = 1 ]; then
    note "compounding/op-log: N/A for a topic base (curate-write only — explicit add, no auto-compound)"
else
for tool in compound.py remember.py oplog.py; do
    if [ -f "$MEMORY_SYSTEM/bin/$tool" ]; then ok "$tool present"; else warn "$tool NOT deployed — promote/op-log unavailable" "sync source bin/ to $MEMORY_SYSTEM/bin/ (re-run install.sh)"; fi
done
fi
# card_kind distribution — confirms typed pages (synthesis/concept/entity) flow.
if [ -f "$DB" ]; then
    KINDS=$(sqlite3 "$DB" "SELECT group_concat(card_kind || '=' || n, ' ') FROM (SELECT card_kind, COUNT(*) n FROM memory_chunks WHERE card_kind IS NOT NULL AND card_kind != '' GROUP BY card_kind ORDER BY n DESC LIMIT 6)" 2>/dev/null)
    [ -n "$KINDS" ] && ok "card_kind in index: $KINDS" || note "no card_kind values yet (reindex to populate)"
fi
# the greppable op-log timeline (grep '^## \[' log.md).
OPLOG="$MEMORY_SYSTEM/log.md"
if [ -f "$OPLOG" ]; then
    OPS=$(grep -c '^## \[' "$OPLOG" 2>/dev/null || echo 0)
    LAST=$(grep '^## \[' "$OPLOG" 2>/dev/null | tail -n1 | cut -c1-66)
    ok "op-log: $OPS ops · last: ${LAST:-none}"
else
    note "op-log not started yet ($OPLOG) — first promote/compound creates it"
fi

# ----------------------------------------------------------------- USAGE
# The READ side: which cards searches actually surface (usage.py logs medium+ hits;
# usage_stats.py aggregates). Answers "is this memory useful" + flags dead cards.
hdr "Usage (which cards get surfaced)"
# a base has no bin/ of its own — fall back to the running install's usage_stats.py
# (queried against the base's own DB), so usage renders for a base too.
USAGE_BIN="$MEMORY_SYSTEM/bin/usage_stats.py"; [ -f "$USAGE_BIN" ] || USAGE_BIN="$SCRIPT_DIR/usage_stats.py"
if [ -f "$USAGE_BIN" ]; then
    USAGE=$(python3 "$USAGE_BIN" --db "$DB" --summary 2>/dev/null)
    if [ -n "$USAGE" ]; then
        SURF=$(echo "$USAGE" | sed -nE 's/.*surfacings=([0-9]+).*/\1/p')
        if [ "${SURF:-0}" -gt 0 ]; then
            note "$USAGE"
            note "report: python3 $USAGE_BIN --top 20   ·   compact: --rollup"
        else
            note "no usage logged yet — searches will start recording (usage_stats.py --top 20)"
        fi
    else
        note "usage stats unavailable (usage_stats.py error)"
    fi
else
    note "usage telemetry not deployed — sync bin/usage*.py to $MEMORY_SYSTEM/bin/"
fi
# Did the logger actually FIRE? (the canary ran a confident search into a TEMP log —
# proves the search→usage.log wiring works, not just that the files exist). Live /
# silent-broken / off / not-deployed. From the §3.1 canary run above.
case "${CANARY_USAGE_STATUS:-}" in
    live)        ok "search tracking VERIFIED live — $CANARY_USAGE_DETAIL" ;;
    silent)      bad "search tracking SILENT — $CANARY_USAGE_DETAIL" "check bin/usage.py + search_impl._log_usage wiring" ;;
    off)         note "search tracking: $CANARY_USAGE_DETAIL" ;;
    notdeployed) note "search tracking: $CANARY_USAGE_DETAIL" ;;
    *)           note "search tracking: not verified (canary skipped — no fastembed/vectors or canary.py absent)" ;;
esac

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
