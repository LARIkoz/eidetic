#!/usr/bin/env python3
"""Eidetic v6 M2 — multi-page synthesis (surgical, provenance-tracked, no laundering).

On ingest of a trigger card T, M2 touches the *related pages* (Karpathy's "an ingest
updates 10–15 pages") — but surgically: it revises ONLY a user-invisible,
sentinel-delimited AGENT synthesis region on each affected MANAGED page, preserving
every user-authored byte verbatim, and it can add AT MOST one tier-1 `observed`
event (never enough to launder a page across the 0.55 injection gate).

Reuses M1's rails with NO engine delta (ENGINE_API stays "1.1"): the v1.1 door
(`Index.neighbors` via `m1_contradiction.neighbors_via_door`), M1's shared
confirmer, and the confidence-event writer (`evidence.append_event`, dark-gated by
EIDETIC_CONFIDENCE_EVENTS). The one structural addition is the fence-hardened,
id-verified `_synthesis_region_bounds` locator (FR-4) — the safety boundary.

Dark-safe (FR-9): with EIDETIC_CONFIDENCE_EVENTS off, M2 is a COMPLETE no-op — no
selection, no page mutation, no event.
"""

import os
import re
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import confidence as _C  # noqa: E402
import evidence as _EV  # noqa: E402
import index_impl as _IDX  # noqa: E402
import m1_contradiction as _M1  # noqa: E402

try:
    import oplog as _OPLOG
except Exception:  # pragma: no cover
    _OPLOG = None

try:
    from constants import (M2_FANOUT, M2_RELATED_MIN, M2_RELATED_MIN_DEFAULT,
                           M2_RELEVANCE_MIN, M2_RELEVANCE_MIN_DEFAULT)
except ImportError:  # pragma: no cover
    M2_FANOUT = 8
    M2_RELATED_MIN = {"multilingual": 0.78, "english": 0.62}
    M2_RELATED_MIN_DEFAULT = 0.78
    M2_RELEVANCE_MIN = {"multilingual": 0.0, "english": 0.0}
    M2_RELEVANCE_MIN_DEFAULT = 0.0

AUTOMATED_ACTOR = "test"  # tier-2 for the supersession terminal (like M1)


def m2_enabled():
    """M2 activation switch — dormant by default (like M1's EIDETIC_M1_CONTRADICTION).
    A STRICTER gate ON TOP of the FR-9 EIDETIC_CONFIDENCE_EVENTS dark rail: M2
    mutates page CONTENT (higher-consequence than M1's event metadata), so it stays
    a complete no-op until BOTH flags are on. Flip only after the mini-conveyor
    converges precision on realistic corpora (spec §6). Dark-safe (FR-9) holds
    either way — off ⇒ no selection, no mutation, no event."""
    return os.environ.get("EIDETIC_M2_SYNTHESIS", "").strip().lower() in (
        "1", "on", "true", "yes")


def _active():
    return _EV.events_enabled() and m2_enabled()

# --- the user-invisible, nonce-stamped sentinel pair (FR-4) ------------------
# HTML comments render to nothing; the begin marker carries a per-region
# CRYPTO-RANDOM id (not time/sequence-derived) recorded in the card frontmatter
# (`synthesis_region_id`). A pair is M2's region ONLY IF its id == that frontmatter
# id, so everything between an id-verified pair is agent bytes M2 itself wrote.
_BEGIN_RE = re.compile(r"<!--\s*eidetic:synthesis:begin\s+id=(\S+).*?-->\s*$")
_END_RE = re.compile(r"<!--\s*eidetic:synthesis:end\s*-->\s*$")
_END_SENTINEL = "<!-- eidetic:synthesis:end -->"


def _begin_sentinel(rid):
    return (f"<!-- eidetic:synthesis:begin id={rid} "
            "(auto-generated — do NOT edit; content between these markers is overwritten) -->")


def _mint_id():
    return secrets.token_hex(16)


def _fence_state(line, in_fence, fence_char, fence_len):
    """Advance the char+length fence state (index_impl.split_sections discipline):
    open on any ``` / ~~~ run when not fenced; close ONLY when the marker char
    matches and its length ≥ the opening run. Returns (in_fence, char, len)."""
    m = re.match(r"^\s*(`{3,}|~{3,})", line)
    if not m:
        return in_fence, fence_char, fence_len
    marker = m.group(1)
    mc, ml = marker[0], len(marker)
    if in_fence and mc == fence_char and ml >= fence_len:
        return False, "", 0
    if not in_fence:
        return True, mc, ml
    return in_fence, fence_char, fence_len  # mismatched marker inside a fence: ignored


def _scan_sentinels(content):
    """Return (begins, ends) lists of dicts for sentinels OUTSIDE code fences.
    begins: {id, line_start, line_end}; ends: {line_start, line_end}. line_end is
    the byte offset just past the line's trailing newline (the region-inner edge)."""
    begins, ends = [], []
    in_fence, fence_char, fence_len = False, "", 0
    offset = 0
    for line in content.splitlines(keepends=True):
        was_fenced = in_fence
        in_fence, fence_char, fence_len = _fence_state(line, in_fence, fence_char, fence_len)
        # a fence marker line is itself never a sentinel; content lines only when
        # NOT inside a fence (and not the opening fence line).
        if not was_fenced and not in_fence:
            stripped = line.strip()
            bm = _BEGIN_RE.match(stripped)
            em = _END_RE.match(stripped)
            if bm:
                begins.append({"id": bm.group(1), "line_start": offset,
                               "line_end": offset + len(line)})
            elif em:
                ends.append({"line_start": offset, "line_end": offset + len(line)})
        offset += len(line)
    return begins, ends


def _synthesis_region_bounds(content, region_id):
    """FR-4 locator. Return (inner_start, inner_end) — the byte range STRICTLY
    BETWEEN an id-verified begin/end sentinel pair whose begin id == `region_id` —
    or None ("no valid region", FAIL-CLOSED). Requires EXACTLY one begin and EXACTLY
    one end (outside fences), the begin's id to match, and end after begin. Any
    deviation (no id / unknown id / duplicate / nested / missing end) → None; the
    caller then creates a fresh region at the safe anchor and NEVER replaces to EOF."""
    if not region_id:
        return None
    begins, ends = _scan_sentinels(content)
    if len(begins) != 1 or len(ends) != 1:
        return None  # missing / duplicate / nested → fail-closed
    b, e = begins[0], ends[0]
    if b["id"] != region_id:
        return None  # forged / mismatched id → not M2's region
    if e["line_start"] < b["line_end"]:
        return None  # end before begin → malformed
    return b["line_end"], e["line_start"]


# --- frontmatter helpers (synthesis_region_id + superseded_by) ---------------
def _frontmatter_span(content):
    """(fm_inner_start, fm_inner_end) byte offsets of the frontmatter body between
    the opening `---\\n` and the closing `\\n---`, or None if no frontmatter."""
    if not content.startswith("---"):
        return None
    end = content.find("\n---", 3)
    if end == -1:
        return None
    return 4, end  # content[4:end] is the frontmatter body (after "---\n")


def _read_frontmatter_key(content, key):
    span = _frontmatter_span(content)
    if span is None:
        return None
    body = content[span[0]:span[1]]
    for line in body.split("\n"):
        m = re.match(rf"^\s*{re.escape(key)}:\s*(.+?)\s*$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


def _set_frontmatter_key(content, key, value):
    """Insert or replace `key: value` in the frontmatter. Creates a minimal
    frontmatter block if the file has none. Only touches the frontmatter."""
    span = _frontmatter_span(content)
    line = f"{key}: {value}"
    if span is None:
        return f"---\n{line}\n---\n\n" + content
    body = content[span[0]:span[1]]
    lines = body.split("\n")
    for i, ln in enumerate(lines):
        if re.match(rf"^\s*{re.escape(key)}:\s*", ln):
            lines[i] = line
            new_body = "\n".join(lines)
            return content[:span[0]] + new_body + content[span[1]:]
    # append as a new frontmatter line (before the closing ---)
    if lines and lines[-1] == "":
        lines[-1] = line
        lines.append("")
    else:
        lines.append(line)
    new_body = "\n".join(lines)
    return content[:span[0]] + new_body + content[span[1]:]


def read_region_id(content):
    return _read_frontmatter_key(content, "synthesis_region_id")


# --- region create / revise (byte-splice; user bytes agent-free) -------------
def _wrap(rid, region_body):
    return f"{_begin_sentinel(rid)}\n{region_body.rstrip()}\n{_END_SENTINEL}"


def apply_region(content, region_body):
    """Create-or-revise the synthesis region, returning (new_content, region_id,
    op). REVISE (op='revise') when the frontmatter id resolves a valid region:
    replace ONLY the between-sentinel bytes. Otherwise CREATE (op='create'): mint a
    fresh id, record it in frontmatter, append an id-stamped pair AFTER all existing
    bytes (safe anchor). FAIL-CLOSED: a malformed/forged pair is left untouched and
    a fresh region is opened instead of ever replacing to EOF."""
    rid = read_region_id(content)
    if rid:
        bounds = _synthesis_region_bounds(content, rid)
        if bounds is not None:
            inner_start, inner_end = bounds
            new_inner = f"{region_body.rstrip()}\n"
            return content[:inner_start] + new_inner + content[inner_end:], rid, "revise"
    # CREATE fresh region at the safe anchor (EOF, after all user bytes)
    new_rid = _mint_id()
    stamped = _set_frontmatter_key(content, "synthesis_region_id", new_rid)
    if not stamped.endswith("\n"):
        stamped += "\n"
    return stamped + "\n" + _wrap(new_rid, region_body) + "\n", new_rid, "create"


def current_region_body(content):
    rid = read_region_id(content)
    if not rid:
        return None
    bounds = _synthesis_region_bounds(content, rid)
    if bounds is None:
        return None
    return content[bounds[0]:bounds[1]]


# --- editability / selection (FR-1, FR-2) ------------------------------------
# M2.1 F2: cards M2 must NEVER edit even though they are "managed" for the
# confidence lifecycle — `feedback` behavioral rules inject into EVERY session's
# context, and `todo`/`handoff` are transient session state. M2 only revises DURABLE
# knowledge (project/finding/synthesis). This is STRICTER than confidence.is_managed
# (which stays as-is — feedback still carries a lifecycle), applied at M2's edit gate.
_M2_TRANSIENT_KINDS = frozenset({"todo", "handoff"})


def is_editable(rec):
    """A page is editable ONLY if it is a MANAGED, DURABLE page: agent-extracted
    non-exempt project/finding/synthesis. user + exempt (reference/concept/entity/
    imported, FR-2) are read-only context; and — M2.1 — `feedback` (behavioral rule,
    injected every session) and `todo`/`handoff` (transient) are ALSO never edited."""
    t = (rec.get("type") or "").strip().lower()
    if t in ("user", "feedback"):
        return False
    if (rec.get("card_kind") or "").strip().lower() in _M2_TRANSIENT_KINDS:
        return False
    return _C.is_managed(rec.get("type"), rec.get("source"), rec.get("card_kind"))


def _profile():
    try:
        import engine
        return engine.profile()
    except Exception:
        return "multilingual"


def related_min():
    return M2_RELATED_MIN.get((_profile() or "").strip().lower(), M2_RELATED_MIN_DEFAULT)


def relevance_min():
    return M2_RELEVANCE_MIN.get((_profile() or "").strip().lower(), M2_RELEVANCE_MIN_DEFAULT)


# --- M2.1 F1: the relevance gate (cross-encoder confirmation on the EDIT) -----
def _default_relevance(a_text, b_text):
    """Default relevance scorer: the S5 cross-encoder logit `engine.rerank(a,[b])[0]`.
    FAIL-CLOSED: returns None if the reranker is unavailable / SOFT-returns [] / errors
    — the caller then SKIPS the edit (never cosine-only). Because the reranker is
    absent on some hosts (ONNX missing), M2 is safe-by-default: no reranker ⇒ no edits."""
    try:
        import engine
        s = engine.rerank(a_text or "", [b_text or ""])
    except Exception:
        return None
    if not s:
        return None
    try:
        return float(s[0])
    except (TypeError, ValueError):
        return None


_ACTIVE_RELEVANCE = None


def register_relevance(fn):
    """Install a relevance scorer (e.g. a provisioned reranker, or a test mock).
    None ⇒ the built-in `_default_relevance` (engine.rerank, fail-closed)."""
    global _ACTIVE_RELEVANCE
    _ACTIVE_RELEVANCE = fn


def active_relevance():
    return _ACTIVE_RELEVANCE or _default_relevance


def select_related(card_path, neighbors):
    """FR-1: dedup by path (best score), drop self, gate by score ≥ M2_RELATED_MIN,
    order deterministically (score desc, normalized_slug), cap at M2_FANOUT."""
    best = {}
    for hit in (neighbors or []):
        p = hit.get("path")
        if not p or p == card_path:
            continue
        best[p] = max(best.get(p, -1.0), float(hit.get("score", 0.0)))
    floor = related_min()
    cand = [(p, s) for p, s in best.items() if s >= floor]
    # (score desc, normalized_slug asc) — deterministic
    cand.sort(key=lambda ps: (-ps[1], _slug_of(ps[0])))
    return cand[:M2_FANOUT]


def _slug_of(path):
    rec = _M1._record_from_file(path)
    return rec["slug"] if rec else os.path.basename(path)


# --- provenance (FR-5) -------------------------------------------------------
def _iso_date():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


def _provenance_line(trigger, score):
    return (f"_M2 synthesis · trigger={trigger['slug']} · source={trigger['source']} "
            f"· {_iso_date()} · score={score:.3f}_")


def _clean_oneliner(s, limit=90):
    """Strip ALL markdown artifacts (bold/italic/code/strike, leading heading/list/
    quote markers, wiki/link brackets), collapse whitespace, and cap at a WORD
    boundary ≤ limit (never a mid-word cut, no trailing emphasis/punctuation)."""
    s = re.sub(r"\*\*|__|~~|`", "", s or "")            # bold / code / strike
    s = re.sub(r"(?<![A-Za-z0-9])[*_](?![A-Za-z0-9])", "", s)  # stray emphasis
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)     # [text](url) → text
    s = s.replace("[[", "").replace("]]", "")           # wikilink brackets
    s = re.sub(r"^\s*[#>\-*•\d.\)]+\s*", "", s)          # leading heading/list/quote
    s = " ".join(s.split())                              # collapse whitespace
    if len(s) <= limit:
        return s.rstrip(" .,;:—-")
    cut = s[:limit].rsplit(" ", 1)[0].rstrip(" .,;:—-")  # back off to a word boundary
    return (cut or s[:limit]) + "…"


def _salient_claim(rec, limit=90):
    """The card's salient claim: the first meaningful body line, CLEANED of all
    markdown; falls back to the card name/slug when the first line is noise. The
    output is markdown-free, whitespace-collapsed, word-boundary-capped, and
    deterministic (⇒ the consolidation body is deterministic, FR-8 idempotence)."""
    for raw in (rec.get("text") or "").splitlines():
        s = raw.strip()
        if not s or s.startswith("<!--") or s.startswith("|") or s.startswith("---"):
            continue
        low = s.lstrip("#").strip().lower()
        if low.startswith("## evidence") or low.startswith("m2 synthesis"):
            continue
        cleaned = _clean_oneliner(s, limit)
        if len(cleaned) >= 3:
            return cleaned
    # first line was noise → prefer the card name, else slug
    name = (rec.get("name") or "").strip()
    return _clean_oneliner(name, limit) if name else (rec.get("slug") or "(no summary)")


def _default_synthesis_body(trigger, target, provenance):
    """D1 — the DEFAULT (LLM-free, 0-API-token) DETERMINISTIC consolidation writer.
    Gathers the trigger + the co-related MANAGED pages' salient claim lines
    (`target["related"]`), dedups by slug, and writes a bounded provenance-stamped
    consolidation of `[[link]] — one-liner` rows in a deterministic (slug) order.
    REPLACES the region each synthesis (convergence, not accretion) → identical
    inputs ⇒ identical body ⇒ FR-8 skips the edit + the event; bounded by K rows so
    the region does not grow with M re-syntheses. Optional LLM path stays behind the
    `synth_body_fn` seam, OFF by default; if ever wired it still writes ONLY here and
    still ≤ one `observed`."""
    entries, seen = [], set()
    rows = [{"slug": trigger["slug"], "salient": _salient_claim(trigger)}]
    rows += list(target.get("related") or [])
    for item in rows:
        s = item.get("slug")
        if not s or s in seen:
            continue
        seen.add(s)
        entries.append((s, item.get("salient") or ""))
    lines = [provenance, "", "Consolidated related context (auto-synthesized):"]
    for s, salient in sorted(entries):  # deterministic slug order
        lines.append(f"- [[{s}]] — {salient}" if salient else f"- [[{s}]]")
    return "\n".join(lines)


# D4 — op-log schema. Canonical M2 op verbs (greppable, stable vocabulary).
OP_SYNTHESIS_EDIT = "synthesis_edit"
OP_SUPERSESSION = "supersession"
OP_SUPERSESSION_SUGGESTION = "supersession_suggestion"
OP_CONTRADICTION_DEFERRAL = "contradiction_deferral"
OP_REGION_BROKEN = "region_broken"  # F1: user-broken region skipped (surfaced once)
OP_RELEVANCE_SKIPPED = "relevance_skipped"  # M2.1: below the reranker relevance gate


def _log_path_for(index_db_path):
    """The op-log lives at the memory-system root (`<root>/log.md`); the index db is
    `<root>/db/index.db`. Deriving it from index_db_path keeps tests HERMETIC (temp
    log) and never touches the live global log. None ⇒ no db path ⇒ skip the op-log
    (never fall back to the global default — that would write to the live store)."""
    if not index_db_path:
        return None
    return os.path.join(os.path.dirname(os.path.dirname(index_db_path)), "log.md")


def _oplog(index_db_path, op, target_slug, *, trigger=None, score=None, extra=None):
    """Mirror an M2 operation to the op-log with the D4 schema:
    op ∈ {synthesis_edit, supersession, supersession_suggestion, contradiction_deferral},
    trigger id/source, ISO date, score, target slug. Written ONLY on an actual
    op (not idempotent skips), so re-runs add no duplicate rows. Best-effort; the
    durable provenance is the in-file region line (survives reindex, AC-7)."""
    log_path = _log_path_for(index_db_path)
    if _OPLOG is None or log_path is None:
        return
    bits = [f"op={op}", f"target={target_slug}", f"date={_iso_date()}"]
    if trigger is not None:
        bits.append(f"trigger={trigger.get('slug')}")
        bits.append(f"source={trigger.get('source')}")
    if score is not None:
        bits.append(f"score={score:.3f}")
    if extra:
        bits.append(extra)
    try:
        _OPLOG.append_op(op, target_slug, detail=" ".join(bits), log_path=log_path)
    except Exception:
        pass


def _oplog_once(index_db_path, op, target_slug, *, trigger=None):
    """Like `_oplog`, but DEDUPED by (op, target): if the log already carries an
    `op=<op> target=<slug>` entry it is NOT re-appended. Used for the F1
    broken-region suggestion so a user-corrupted region is surfaced exactly once,
    not re-logged every ingest (no op-log growth). Best-effort; hermetic."""
    log_path = _log_path_for(index_db_path)
    if _OPLOG is None or log_path is None:
        return
    key = f"op={op} target={target_slug}"
    try:
        if os.path.exists(log_path) and key in open(log_path, encoding="utf-8").read():
            return  # already surfaced — dedup, no op-log growth
    except OSError:
        pass
    bits = [key, f"date={_iso_date()}"]
    if trigger is not None:
        bits.append(f"trigger={trigger.get('slug')}")
        bits.append(f"source={trigger.get('source')}")
    try:
        _OPLOG.append_op(op, target_slug, detail=" ".join(bits), log_path=log_path)
    except Exception:
        pass


# --- supersession (FR-7) -----------------------------------------------------
_SHIPPED = ("shipped", "released", "launched", "completed", "done", "landed", "ga", "live")
_PLANNED = ("plan", "planned", "will ", "todo", "proposed", "intend", "going to",
            "upcoming", "roadmap", "draft", "wip")
_VER_RE = re.compile(r"\bv?(\d+(?:\.\d+)*)\b")
_YEAR_RE2 = re.compile(r"\b(19|20)\d{2}\b")


def _max_version(text):
    best = None
    for m in _VER_RE.finditer(text or ""):
        tup = tuple(int(x) for x in m.group(1).split("."))
        if best is None or tup > best:
            best = tup
    return best


def _max_year(text):
    yrs = [int(m.group(0)) for m in _YEAR_RE2.finditer(text or "")]
    return max(yrs) if yrs else None


def _shared_subject(trigger, target):
    """The pair is about the SAME topic: the trigger slug stem appears in the target
    text (or vice-versa). Prevents a version/date bump on an UNRELATED page from
    reading as a supersession."""
    p = (target.get("text") or "").lower()
    t = (trigger.get("text") or "").lower()
    stem = (trigger.get("slug") or "").split("-")[0]
    tstem = (target.get("slug") or "").split("-")[0]
    return bool(stem) and (stem in p or (bool(tstem) and tstem in t))


def _default_supersedes(trigger, target):
    """LLM-free temporal-EVOLUTION classifier (§8 dual-purpose: the confirmer emits a
    conflict XOR M2 proposes a supersession). Returns True on a clear evolution over
    a SHARED subject — plan→shipped, a higher version (v1→v2), or a later year —
    else False (fail-closed: a missed supersession is safe, an FP retires a page).
    Only PROPOSES; the FR-7 authority gate still disposes. A true semantic opposition
    never reaches here — M1's confirmer classifies it `contradiction` first (XOR)."""
    t = (trigger.get("text") or "").lower()
    p = (target.get("text") or "").lower()
    if not _shared_subject(trigger, target):
        return False
    # plan → shipped
    if any(w in t for w in _SHIPPED) and any(w in p for w in _PLANNED):
        return True
    # version bump: trigger carries a strictly HIGHER version than the target
    tv, pv = _max_version(t), _max_version(p)
    if tv is not None and pv is not None and tv > pv:
        return True
    # date evolution: trigger carries a strictly LATER year than the target
    ty, py = _max_year(t), _max_year(p)
    if ty is not None and py is not None and ty > py:
        return True
    return False


def _authority_dominates(trigger, target):
    """§4.4: the superseding source dominates when its authority tier is higher,
    or equal-and-newer (last_verified)."""
    ta, pa = trigger.get("authority", 2), target.get("authority", 2)
    if ta != pa:
        return ta > pa
    return (trigger.get("last_verified") or "") > (target.get("last_verified") or "")


def _apply_supersession(index_db_path, trigger, target):
    """FR-7: M2 is the SOLE auto-setter of superseded_by. Set target.superseded_by
    → trigger, and append ONE terminal `contradicted` event (tier-2, note marking a
    SUPERSESSION so the §8 truth-slice join disambiguates it from an M1 semantic
    contradiction). Returns 'superseded' or 'supersession_suggested'."""
    if not _authority_dominates(trigger, target):
        _oplog(index_db_path, OP_SUPERSESSION_SUGGESTION, target["slug"], trigger=trigger,
               extra="reason=non-dominating-authority")
        return "supersession_suggested"
    path = target["path"]
    with _EV.card_lock(path) as held:  # D3: spool-under-lock (read-modify-write atomic)
        if not held:
            print(f"WARN: M2 could not lock {path}; supersession skipped (no lost update)",
                  file=sys.stderr)
            return "lock_contended"
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            return "supersession_suggested"
        if _read_frontmatter_key(content, "superseded_by") == trigger["slug"]:
            return "idempotent_skip"  # already superseded by this trigger
        _atomic_write(path, _set_frontmatter_key(content, "superseded_by", trigger["slug"]))
        _EV.append_event(path, "contradicted", actor=AUTOMATED_ACTOR,
                         note=f"superseded by {trigger['slug']} (m2 supersession terminal)",
                         _locked=True)
    _oplog(index_db_path, OP_SUPERSESSION, target["slug"], trigger=trigger)
    return "superseded"


def _atomic_write(path, content):
    try:
        import remember
        remember._atomic_write(path, content)
    except Exception:
        import tempfile
        d = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".m2tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)


# --- the pipeline (FR-1..FR-9) -----------------------------------------------
def process_trigger(index_db_path, trigger_path, meta, body, *, neighbors,
                    confirmer=None, supersedes=None, synth_body_fn=None,
                    relevance_fn=None):
    """Run M2 for one trigger T against its `neighbors`. Returns outcome dicts.
    DARK-SAFE (FR-9): a complete no-op unless EIDETIC_CONFIDENCE_EVENTS is on AND
    the M2 activation flag is set (dormant by default)."""
    if not _active():
        return []
    confirmer = confirmer or _M1.production_confirmer
    supersedes = supersedes or _default_supersedes
    synth_body_fn = synth_body_fn or _default_synthesis_body
    relevance_fn = relevance_fn or active_relevance()
    floor = relevance_min()
    T = _M1._record(trigger_path, meta, body)
    outcomes = []

    # Resolve the selected set once and classify editability (FR-2).
    selected = []
    for path, score in select_related(trigger_path, neighbors):
        P = _M1._record_from_file(path)
        if P is not None:
            selected.append((path, score, P))

    # M2.1 F1: the RELEVANCE gate. The cosine gate (M2_RELATED_MIN) is recall-only;
    # a cross-encoder must confirm P is GENUINELY related to T before M2 edits it.
    # Compute per editable neighbor (fail-closed None) and admit only those ≥ floor;
    # the consolidation body then references ONLY relevance-passed co-related pages.
    # M2.1-R1: the memo + the consolidation set are keyed by PATH (unique), NOT slug
    # — `select_related` dedups by path but slugs collide (two distinct files can share
    # a `name:`), so slug-keying would let a below-floor page borrow a same-slug page's
    # score (a spurious edit) or wrongly skip a relevant one. The `[[slug]]` DISPLAY
    # text stays slug-based (cosmetic); every relevance DECISION + body-inclusion is
    # per-path.
    def _rel(P):
        try:
            return relevance_fn(T.get("text", ""), P.get("text", ""))
        except Exception:
            return None
    rel_by_path = {}
    editable_claims = []
    for p_path, _s, P in selected:
        if is_editable(P):
            r = _rel(P)
            rel_by_path[p_path] = r
            if r is not None and r >= floor:
                editable_claims.append({"path": p_path, "slug": P["slug"],
                                        "salient": _salient_claim(P)})

    for path, score, P in selected:
        if not is_editable(P):
            outcomes.append({"path": path, "action": "read_only_context"})
            continue  # FR-2: user/exempt/feedback/transient never edited, never event'd

        # FR-3: a true contradiction is NEVER resolved by overwriting text — hand
        # the pair to M1's contradicted path (idempotent; M1 owns the emit).
        try:
            verdict = confirmer(T, P)
        except Exception:
            verdict = "no_contradiction"  # fail-closed
        if verdict == "contradiction":
            _M1.process_card(trigger_path, meta, body,
                             neighbors=[{"score": score, "path": path}],
                             confirmer=confirmer, index_db_path=index_db_path)
            _oplog(index_db_path, OP_CONTRADICTION_DEFERRAL, P["slug"], trigger=T, score=score)
            outcomes.append({"path": path, "action": "deferred_to_m1"})
            continue

        # FR-7: gated temporal supersession (M2 is the sole superseded_by setter).
        try:
            is_sup = supersedes(T, P)
        except Exception:
            is_sup = False
        if is_sup:
            act = _apply_supersession(index_db_path, T, P)
            outcomes.append({"path": path, "action": act})
            continue

        # M2.1 F1: relevance gate on the EDIT. FAIL-CLOSED — no reranker / None /
        # below floor ⇒ SKIP (no synthesis, no event), surfaced once (deduped).
        # M2.1-R1: look up THIS path's own reranker score (never a same-slug page's).
        r = rel_by_path.get(path)
        if r is None or r < floor:
            _oplog_once(index_db_path, OP_RELEVANCE_SKIPPED, P["slug"], trigger=T)
            outcomes.append({"path": path, "action": "relevance_skipped", "rel": r})
            continue

        # FR-4/FR-5/FR-6: revise the synthesis region + one `observed` event. The
        # consolidation references the co-related relevance-passed pages (exclude self
        # BY PATH, so a same-slug distinct page is handled independently).
        related = [e for e in editable_claims if e["path"] != path]
        outcomes.append(_edit_page(index_db_path, path, T, P["slug"], score,
                                   synth_body_fn, related))
    return outcomes


def _edit_page(index_db_path, path, trigger, target_slug, score, synth_body_fn, related):
    """FR-4/5/6 under D3 spool-under-lock: the whole region read-modify-write AND the
    `observed` event happen inside ONE hold of the card's persistent flock, so two
    concurrent M2 edits to the same card serialize losslessly (no interleave, no
    lost update). A stuck holder → fail LOUD, never a silent drop."""
    outcome = None
    with _EV.card_lock(path) as held:
        if not held:
            print(f"WARN: M2 could not lock {path}; edit skipped (no lost update)",
                  file=sys.stderr)
            return {"path": path, "action": "lock_contended"}
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            return {"path": path, "action": "unreadable"}

        # F1 (A1.7): the frontmatter `synthesis_region_id` IS present but the locator
        # cannot resolve it to a clean region (the user hand-broke it — e.g. deleted
        # the end sentinel, or a duplicate). Do NOT append a fresh region (that grows
        # the page unboundedly every ingest) and do NOT guess a boundary / self-heal
        # (that reintroduces the ambiguity fail-closed exists to avoid). SKIP: no
        # mutation, no new region, no growth; surface ONCE via a deduped op-log
        # suggestion. (An ABSENT id — first synthesis, or a forged pair with no
        # matching id (AC-2e) — still falls through to a fresh create.)
        rid = read_region_id(content)
        if rid and _synthesis_region_bounds(content, rid) is None:
            outcome = {"path": path, "action": "broken_region_skipped"}
        else:
            provenance = _provenance_line(trigger, score)
            region_body = synth_body_fn(trigger, {"slug": target_slug, "related": related},
                                        provenance)
            # FR-8 idempotence: deterministic body → if the region already equals what
            # we would write, skip the edit AND the event (append_event stamps a fresh
            # ts, so the PK cannot dedup — this explicit content guard must).
            cur = current_region_body(content)
            if cur is not None and cur.strip() == region_body.rstrip():
                return {"path": path, "action": "idempotent_skip"}
            new_content, new_rid, op = apply_region(content, region_body)
            if new_content == content:
                return {"path": path, "action": "idempotent_skip"}
            _atomic_write(path, new_content)
            # FR-6 NO-LAUNDER: at most ONE tier-1 `observed` (+0.05, capped) — never
            # confirmed/verified_by_test. _locked: we already hold this card's flock.
            _EV.append_event(path, "observed", actor="agent-extracted",
                             note=f"m2 synthesis from {trigger['slug']}", _locked=True)
            outcome = {"path": path, "action": "edited", "op": op, "region_id": new_rid}
    # lock released → mirror to the op-log (its own flock).
    if outcome["action"] == "broken_region_skipped":
        _oplog_once(index_db_path, OP_REGION_BROKEN, target_slug, trigger=trigger)
    else:
        _oplog(index_db_path, OP_SYNTHESIS_EDIT, target_slug, trigger=trigger, score=score,
               extra=f"region={outcome['op']}")
    return outcome


# --- ingest hook (FR-1/FR-9) -------------------------------------------------
def run_on_ingest(conn, index_db_path, changed_paths, confirmer=None, supersedes=None,
                  relevance_fn=None):
    """Ingest hook. Dark-safe: no-op unless EIDETIC_CONFIDENCE_EVENTS is on. Runs
    AFTER M1 in run_incremental so M1 owns contradictions and M2 defers to it.
    Never raises into the indexer."""
    if not _active():
        return
    for path in changed_paths:
        try:
            rec = _M1._record_from_file(path)
            if rec is None:
                continue
            try:
                import engine
                probe = engine.embedding_text(rec["name"], "", rec["text"], "")
            except Exception:
                probe = f"{rec['name']}\n{rec['text']}"
            hits = _M1.neighbors_via_door(index_db_path, probe, exclude_paths={path})
            if hits:
                process_trigger(index_db_path, path,
                                {"name": rec["name"], "type": rec["type"],
                                 "source": rec["source"],
                                 "last_verified": rec["last_verified"]},
                                rec["text"], neighbors=hits,
                                confirmer=confirmer, supersedes=supersedes,
                                relevance_fn=relevance_fn)
        except Exception as e:
            print(f"WARN: M2 skipped {path}: {e}", file=sys.stderr)
