#!/usr/bin/env python3
"""AI Memory System v1 — Compounding Logic (FR4.1, Karpathy).

Reads signals from stdin, for each:
1. Search FTS5 for existing memory on same topic
2. If match found → update existing file + add History section
3. If no match → create new signal file
4. Source: agent-extracted (0.5x self-referential discount)

"Humans abandon wikis because maintenance grows faster than value. LLMs don't get bored."
"""

import os
import re
import sqlite3
import sys
from datetime import datetime

def default_memory_system():
    installed_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if os.path.exists(os.path.join(installed_root, ".installed.json")):
        return installed_root
    return os.path.expanduser("~/.claude/memory-system")


MEMORY_SYSTEM = os.path.expanduser(
    os.environ.get("EIDETIC_MEMORY_SYSTEM") or default_memory_system()
)
DB_PATH = os.path.join(MEMORY_SYSTEM, "db", "index.db")
TODAY = datetime.now().strftime("%Y-%m-%d")


# Threshold fallback (stage 3): >= COMPOUND overlapping salient keywords on
# one card → compound into it; exactly FLAG → too ambiguous to auto-compound,
# but too close to silently duplicate — log "possible duplicate of <card>"
# and still create the new file. Below FLAG → genuinely new topic.
OVERLAP_COMPOUND_MIN = 3
OVERLAP_FLAG_MIN = 2
# Vector gate = POSITIVE-ONLY promotion threshold (never a veto).
#
# Design law (audit findings, KEEP #6): the vector gate may ADD positive signal
# (promote a BORDERLINE lexical match the salient-keyword counter under-counted)
# or stay silent — it must NEVER hard-veto a STRONG lexical match. A cosine at
# or above this threshold is treated as "the embedder judges this a true
# duplicate"; below it the gate is silent and the lexical stage alone decides.
#
# Threshold calibrated empirically ON THIS BOX (multilingual-e5-large, 1024d,
# fastembed 0.8.0, query:/passage: prefixes — see eidetic-v6-build calibration
# 2026-07-03): a genuine same-topic duplicate scored cos 0.920, while topical-
# but-distinct near-dups sharing surface keywords scored 0.833-0.837 and a
# strong-lexical paraphrase of the target scored 0.843. 0.85 therefore cleanly
# separates a real duplicate (>=0.92) from e5 topical noise (~0.83-0.84) — and,
# critically, sits ABOVE the 0.843 strong-lexical paraphrase, proving why the
# gate must not veto strong lexical matches (they legitimately score below it).
# bge-small-en spreads wider and 0.60 discriminates there. Unknown profile →
# strict (fail toward FLAGGING, never toward auto-compounding).
VECTOR_GATE_MIN_SIM_BY_PROFILE = {"multilingual": 0.85, "english": 0.60}
VECTOR_GATE_MIN_SIM_DEFAULT = 0.85


def _sanitize_words(query):
    # Keep `-`: a hyphenated identifier (scikit-learn) stays ONE keyword unit,
    # matching extract_keywords — splitting it here double-counted both halves
    # in the overlap threshold. Hyphens are safe inside the quoted FTS terms
    # every caller builds (porter unicode61 splits them into adjacent tokens).
    if not isinstance(query, str):
        query = " ".join(query)
    sanitized = re.sub(r'[*()\[\]{}^~:+]', ' ', query)
    sanitized = sanitized.replace('"', '""')
    return [w for w in sanitized.split() if len(w) > 2 and w.upper() not in ("AND", "OR", "NOT", "NEAR")]


def _keyword_paths(conn, word, limit=200):
    """Distinct card paths matching ONE keyword (FTS porter stemming applies,
    so `performing` finds a card that says `performs`). ORDER BY path before
    LIMIT: an unordered LIMIT returned an arbitrary subset, so the same signal
    could compound on one run and duplicate on the next."""
    try:
        rows = conn.execute("""
            SELECT DISTINCT c.path
            FROM memory_fts
            JOIN memory_chunks c ON memory_fts.rowid = c.id
            WHERE memory_fts MATCH ?
            ORDER BY c.path
            LIMIT ?
        """, ('"' + word + '"', limit)).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {r[0] for r in rows}


def _salient_words(conn, words):
    """Order keywords by salience: rarest-in-corpus first (document frequency
    via the porter-stemmed FTS index), longer word on ties, signal order last.

    v5.13.0 used the FIRST 4 keywords in signal order, so a paraphrase that
    swapped one early word silently duplicated (audit probe E2C). Keywords the
    corpus has never seen (df=0) are dropped — they carry no matching power
    and would sabotage every AND query.

    Returns [(word, matching_paths), ...] most-salient first.
    """
    scored = []
    for position, word in enumerate(words):
        paths = _keyword_paths(conn, word)
        if not paths:
            continue
        scored.append((len(paths), -len(word), position, word, paths))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    return [(word, paths) for _df, _neglen, _pos, word, paths in scored]


def search_fts5(conn, query, limit=3):
    """Search FTS5 for existing memory on same topic.

    Staged match: the strict phrase of up to 6 keywords first (cheap, precise),
    then ONE retry as an implicit AND of the top 4 SALIENT keywords (rarest in
    corpus first — not first-in-signal-order, which was paraphrase-fragile).
    Deliberately NO loose OR stage — false-compounding a signal into an
    unrelated card is worse than creating a new card; partial overlap is
    handled by find_overlap_candidate(), which compounds only above a strict
    threshold and otherwise merely FLAGS.
    """
    words = _sanitize_words(query)
    if not words:
        return []

    def run_match(fts_query):
        try:
            return conn.execute("""
                SELECT c.path, c.name, c.section_heading, c.content,
                       memory_fts.rank AS fts_rank
                FROM memory_fts
                JOIN memory_chunks c ON memory_fts.rowid = c.id
                WHERE memory_fts MATCH ?
                ORDER BY memory_fts.rank
                LIMIT ?
            """, (fts_query, limit)).fetchall()
        except sqlite3.OperationalError:
            return []

    rows = run_match('"' + " ".join(words[:6]) + '"')
    if rows:
        return rows
    salient = _salient_words(conn, words)
    if len(salient) < 4:
        # An AND of fewer than 4 terms is too weak a topic signature to
        # auto-compound on; leave it to the thresholded fallback.
        return []
    # FTS5 space-separated terms = implicit AND; each term quoted so a kept
    # hyphenated identifier parses as a phrase, not FTS syntax.
    return run_match(" ".join('"' + w + '"' for w, _paths in salient[:4]))


_vector_gate_warned = False


def _vector_gate(signal_text, candidate_text):
    """Optional semantic signal for the threshold fallback (positive-only).

    True  = cosine >= the profile promotion threshold (the embedder judges the
            pair a true duplicate) — used only to PROMOTE a borderline lexical
            match, never to veto a strong one (see find_overlap_candidate).
    False = cosine below threshold (embedder does not see a duplicate).
    None  = vectors unavailable (no vectors.db / no fastembed — the FTS-only
            default); caller proceeds on the lexical stage alone.
    Never raises, but a degradation WITH a vectors.db present is logged once
    (class only) — a silently dead gate looks identical to an approving one.

    Asymmetric prefixes: the signal is the QUERY side, the candidate card the
    passage side — embedding both as passage: inflates e5 cosine and defeats
    the threshold. Threshold is profile-aware (see VECTOR_GATE_MIN_SIM_BY_PROFILE).
    """
    global _vector_gate_warned
    vector_db = DB_PATH.replace("index.db", "vectors.db")
    if not os.path.exists(vector_db):
        return None
    try:
        import struct

        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import embed
        q_blobs = embed.embed_query_texts([signal_text[:2000]])
        p_blobs = embed.embed_texts([candidate_text[:2000]])
        if not q_blobs or not p_blobs:
            return None
        vecs = [struct.unpack(f"{len(b) // 4}f", b) for b in (q_blobs[0], p_blobs[0])]
        dot = sum(x * y for x, y in zip(vecs[0], vecs[1]))
        norms = [sum(x * x for x in v) ** 0.5 for v in vecs]
        if not all(norms):
            return None
        threshold = VECTOR_GATE_MIN_SIM_BY_PROFILE.get(
            getattr(embed, "EMBED_PROFILE", ""), VECTOR_GATE_MIN_SIM_DEFAULT
        )
        return (dot / (norms[0] * norms[1])) >= threshold
    except Exception as exc:
        if not _vector_gate_warned:
            _vector_gate_warned = True
            print(
                f"WARN: compound vector gate degraded ({type(exc).__name__}) "
                "despite vectors.db present; using lexical threshold only",
                file=sys.stderr,
            )
        return None


def find_overlap_candidate(conn, query, signal_text):
    """Stage-3 thresholded fallback after phrase + AND both missed.

    Counts how many of the top-6 salient keywords individually hit the same
    card (porter stemming absorbs inflection: performs/performing), then applies
    the vector gate as a POSITIVE-ONLY signal per the KEEP #6 design law:

      STRONG lexical (>= OVERLAP_COMPOUND_MIN keywords converge on one card):
        compound unconditionally. The vector gate may CONFIRM but must NEVER
        veto — a genuine strong-lexical paraphrase scores below e5's duplicate
        line (0.843 < 0.85 on this box), so a cosine veto here demoted real
        compounds (the pre-fix bug: 1 RED test on the vectored box).
      BORDERLINE lexical (OVERLAP_FLAG_MIN .. OVERLAP_COMPOUND_MIN-1): too weak
        to auto-compound on lexical alone, but the vector gate may ADD positive
        signal — a near-dup the salient counter under-counted still compounds
        when the embedder judges the pair a true duplicate (sim >= threshold).
        Otherwise FLAG.
      No lexical (< OVERLAP_FLAG_MIN): genuinely new topic — vector-only
        compounding is NEVER allowed.

    Returns:
      ("compound", rows)  — strong lexical, or borderline confirmed by vectors;
      ("flag", path)      — a possible duplicate: surface it, don't silently dup;
      (None, None)        — genuinely new topic.
    """
    words = _sanitize_words(query)
    salient = _salient_words(conn, words)[:6]
    if not salient:
        return None, None

    counts = {}
    for _word, paths in salient:
        for path in paths:
            if is_compound_candidate(path):
                counts[path] = counts.get(path, 0) + 1
    if not counts:
        return None, None

    best_path, best_count = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    if best_count < OVERLAP_FLAG_MIN:
        return None, None

    def _card_rows():
        return conn.execute("""
            SELECT path, name, section_heading, content, 0 AS fts_rank
            FROM memory_chunks WHERE path = ? LIMIT 1
        """, (best_path,)).fetchall()

    # STRONG lexical: compound; gate may confirm, never veto (so it isn't called).
    if best_count >= OVERLAP_COMPOUND_MIN:
        rows = _card_rows()
        return ("compound", rows) if rows else ("flag", best_path)

    # BORDERLINE lexical: vectors decide. Only a positive (True) cosine promotes
    # to compound; False (below threshold) and None (vectors unavailable) FLAG.
    rows = _card_rows()
    if rows and _vector_gate(signal_text, rows[0][3] or "") is True:
        return "compound", rows
    return "flag", best_path


def extract_keywords(signal_text):
    """Extract meaningful keywords from a signal for FTS5 search.

    Returns the stopword-filtered keywords in signal order (the phrase stage
    needs original order); when over the cap, the LONGEST words are kept
    (local salience). Corpus-rarity ordering happens in _salient_words, where
    the index is available.
    """
    words = re.findall(r'\b[a-zA-Z_-]{4,}\b', signal_text)
    stopwords = {
        "that", "this", "with", "from", "have", "been", "were", "will",
        "would", "could", "should", "about", "their", "which", "when",
        "what", "more", "than", "very", "also", "just", "into", "only",
        "other", "some", "such", "because", "before", "after", "made",
        "then", "them", "they", "there", "these", "those", "where",
        "while", "during", "using", "does", "each", "both", "same",
        "being", "much", "many", "most", "must", "over", "under",
        "between", "through", "against", "without", "within", "instead",
        "every", "several", "always", "never", "still",
        "decision", "rule", "worked", "failed", "knowledge",
    }
    keywords = []
    seen = set()
    for w in words:
        lw = w.lower()
        if lw in stopwords or lw in seen:
            continue
        seen.add(lw)
        keywords.append(w)
    if len(keywords) > 10:
        keep = set(sorted(keywords, key=len, reverse=True)[:10])
        keywords = [w for w in keywords if w in keep]
    return " ".join(keywords)


PROTECTED_TYPES = {"feedback", "user"}
SIGNAL_PREFIX_RE = re.compile(r"^(Decision|Rule|Worked|Failed|Knowledge):\s+\S")


def normalize_signals(raw):
    return [
        line.strip()
        for line in raw.splitlines()
        if SIGNAL_PREFIX_RE.match(line.strip())
    ]


def is_compound_candidate(path):
    """A returned exact FTS match is enough; FTS5 rank magnitudes are corpus-scale dependent."""
    return bool(path and "/memory/" in path and "SKILL.md" not in path)


def _get_file_type(filepath):
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(500)
    except Exception:
        return None
    for line in head.split("\n"):
        m = re.match(r'^\s*type:\s*(\S+)', line.strip())
        if m:
            return m.group(1)
    return None


def _markdown_headings(content):
    in_fence = False
    offset = 0
    for line in content.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            offset += len(line)
            continue
        if not in_fence:
            m = re.match(r'^(##)\s+(.+?)\s*$', line.rstrip("\r\n"))
            if m:
                yield offset, offset + len(line), m.group(2).strip()
        offset += len(line)


def _history_section_bounds(content):
    headings = list(_markdown_headings(content))
    for idx, (_start, end, title) in enumerate(headings):
        if title == "History":
            next_start = headings[idx + 1][0] if idx + 1 < len(headings) else None
            return end, next_start
    return None


def update_existing(filepath, signal_text):
    """Update existing memory file: append to History section. Does NOT update last_verified."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return False

    history_entry = f"- {TODAY}: {signal_text.strip()}\n"

    history_bounds = _history_section_bounds(content)
    if history_bounds:
        _history_end, next_heading_start = history_bounds
        if next_heading_start is not None:
            before = content[:next_heading_start].rstrip()
            after = content[next_heading_start:].lstrip("\n")
            content = before + "\n" + history_entry + "\n" + after
        else:
            content = content.rstrip() + "\n" + history_entry
    else:
        content = content.rstrip() + f"\n\n## History\n\n{history_entry}"

    tmp = None
    try:
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(filepath), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, filepath)
        return True
    except Exception:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def resolve_memory_dir(cwd):
    """Resolve the project memory dir for a cwd, else the global store.

    Shared by the Stop-hook signal path (create_signal_file) and the manual
    promotion path (remember.py) so both write to the same project memory dir.
    """
    sanitized = cwd.rstrip("/").replace("/", "-").lstrip("-")

    memory_dir = None
    projects_dir = os.path.expanduser("~/.claude/projects/")
    if os.path.isdir(projects_dir):
        for d in os.listdir(projects_dir):
            if d == sanitized or d == "-" + sanitized:
                candidate = os.path.join(projects_dir, d, "memory")
                if os.path.isdir(candidate):
                    memory_dir = candidate
                    break
        if not memory_dir:
            for d in os.listdir(projects_dir):
                if sanitized.endswith(d.lstrip("-")) and len(d) > 10:
                    candidate = os.path.join(projects_dir, d, "memory")
                    if os.path.isdir(candidate):
                        memory_dir = candidate
                        break

    if not memory_dir:
        memory_dir = MEMORY_SYSTEM
        os.makedirs(memory_dir, exist_ok=True)
    return memory_dir


def create_signal_file(cwd, signals):
    """Create new signal file for signals without existing matches."""
    memory_dir = resolve_memory_dir(cwd)
    signals_dir = os.path.join(memory_dir, "signals")
    os.makedirs(signals_dir, exist_ok=True)

    filepath = os.path.join(signals_dir, f"{TODAY}.md")

    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            existing = f.read()
        new_lines = [f"- {s.strip()}\n" for s in signals if s.strip() not in existing]
        if new_lines:
            import tempfile
            fd, tmp = tempfile.mkstemp(dir=signals_dir, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(existing.rstrip("\n") + "\n" + "".join(new_lines))
            os.replace(tmp, filepath)
        return filepath

    content = f"""---
name: signals-{TODAY}
description: "Auto-extracted signals from session {TODAY}"
metadata:
  type: project
  evidence: observed
  source: agent-extracted
  last_verified: {TODAY}
---

# Session Signals — {TODAY}

"""
    for signal in signals:
        content += f"- {signal.strip()}\n"

    import tempfile
    fd, tmp = tempfile.mkstemp(dir=signals_dir, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, filepath)

    return filepath


def main():
    cwd = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

    raw = sys.stdin.read().strip()
    if not raw or raw.upper() == "EMPTY":
        return

    signals = normalize_signals(raw)
    if not signals:
        return

    conn = None
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

    compounded = 0
    new_signals = []
    flagged = []

    for signal in signals:
        keywords = extract_keywords(signal)
        matched = False
        # First candidate we could NOT compound into (protected type or a
        # failed write): if nothing else absorbs the signal it becomes a new
        # card, and this near-dup must be FLAGGED — a silent new card next to
        # a matching feedback/user card is exactly the silent-duplication
        # this release promises away.
        dup_candidate = None

        if conn and keywords:
            results = search_fts5(conn, keywords, limit=3)
            if not results:
                action, payload = find_overlap_candidate(conn, keywords, signal)
                if action == "compound":
                    results = payload
                elif action == "flag":
                    flagged.append((payload, signal))
            for path, name, heading, content, rank in results:
                if is_compound_candidate(path):
                    file_type = _get_file_type(path)
                    if file_type in PROTECTED_TYPES:
                        if dup_candidate is None:
                            dup_candidate = path
                        continue
                    if update_existing(path, signal):
                        compounded += 1
                        matched = True
                        break
                    if dup_candidate is None:
                        dup_candidate = path

        if not matched:
            if dup_candidate is not None:
                flagged.append((dup_candidate, signal))
            new_signals.append(signal)

    if new_signals:
        filepath = create_signal_file(cwd, new_signals)

    if conn:
        conn.close()

    total = compounded + len(new_signals)
    if total > 0:
        summary = f"{compounded} compounded, {len(new_signals)} new"
        if flagged:
            summary += f", {len(flagged)} flagged"
        print(f"Signals: {summary}", file=sys.stderr)
        for dup_path, dup_signal in flagged:
            print(f"possible duplicate of {dup_path}: {dup_signal[:80]}", file=sys.stderr)
        # Mirror onto the greppable op-log. Best-effort: never break the live
        # Stop-hook if oplog is missing or the log dir is unwritable.
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import oplog
            oplog.append_op(
                "compound",
                summary,
                project=cwd, count=total,
            )
            for dup_path, dup_signal in flagged:
                oplog.append_op(
                    "compound-flag",
                    f"possible duplicate of {dup_path}",
                    project=cwd, detail=dup_signal[:200], count=1,
                )
        except Exception:
            pass


if __name__ == "__main__":
    main()
