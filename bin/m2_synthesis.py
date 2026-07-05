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
    from constants import M2_FANOUT, M2_RELATED_MIN, M2_RELATED_MIN_DEFAULT
except ImportError:  # pragma: no cover
    M2_FANOUT = 8
    M2_RELATED_MIN = {"multilingual": 0.80, "english": 0.62}
    M2_RELATED_MIN_DEFAULT = 0.80

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
def is_editable(rec):
    """A page is editable ONLY if it is a MANAGED page (§2.3): feedback, or
    agent-extracted non-exempt project/finding/synthesis. user + exempt
    (reference/concept/entity/imported) are read-only context (FR-2)."""
    if (rec.get("type") or "").strip().lower() == "user":
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


def _default_synthesis_body(trigger, target, provenance):
    """The default (LLM-free) region body: a provenance-stamped consolidation
    pointer. REPLACES the region (convergence, not accretion) — a new trigger
    rewrites this body rather than appending under it."""
    return (f"{provenance}\n\n"
            f"This page's synthesis region reflects the latest related update from "
            f"**{trigger['slug']}**.")


def _oplog(op, title, detail):
    if _OPLOG is None:
        return
    try:
        _OPLOG.append_op(op, title, detail=detail)
    except Exception:
        pass


# --- supersession (FR-7) -----------------------------------------------------
def _default_supersedes(trigger, target):
    """Conservative, LLM-free temporal-supersession detector. Returns True only on
    a clear plan→shipped evolution over a shared subject; else False (fail-closed —
    a missed supersession is safe, an FP retires a page)."""
    t = (trigger.get("text") or "").lower()
    p = (target.get("text") or "").lower()
    shipped = any(w in t for w in ("shipped", "released", "launched", "completed", "done", "landed"))
    planned = any(w in p for w in ("plan", "planned", "will ", "todo", "proposed", "intend", "going to"))
    # shared subject: the trigger slug stem appears in the target (same topic)
    stem = (trigger.get("slug") or "").split("-")[0]
    shared = bool(stem) and stem in p
    return shipped and planned and shared


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
        _oplog("m2-supersede-suggest", target["slug"],
               f"suggested superseded_by={trigger['slug']} (lower authority — not applied)")
        return "supersession_suggested"
    try:
        with open(target["path"], "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return "supersession_suggested"
    if _read_frontmatter_key(content, "superseded_by") == trigger["slug"]:
        return "idempotent_skip"  # already superseded by this trigger
    new_content = _set_frontmatter_key(content, "superseded_by", trigger["slug"])
    _atomic_write(target["path"], new_content)
    _EV.append_event(target["path"], "contradicted", actor=AUTOMATED_ACTOR,
                     note=f"superseded by {trigger['slug']} (m2 supersession terminal)")
    _oplog("m2-supersede", target["slug"], f"superseded_by={trigger['slug']}")
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
                    confirmer=None, supersedes=None, synth_body_fn=None):
    """Run M2 for one trigger T against its `neighbors`. Returns outcome dicts.
    DARK-SAFE (FR-9): a complete no-op unless EIDETIC_CONFIDENCE_EVENTS is on AND
    the M2 activation flag is set (dormant by default)."""
    if not _active():
        return []
    confirmer = confirmer or _M1.production_confirmer
    supersedes = supersedes or _default_supersedes
    synth_body_fn = synth_body_fn or _default_synthesis_body
    T = _M1._record(trigger_path, meta, body)
    outcomes = []

    for path, score in select_related(trigger_path, neighbors):
        P = _M1._record_from_file(path)
        if P is None:
            continue
        if not is_editable(P):
            outcomes.append({"path": path, "action": "read_only_context"})
            continue  # FR-2: user/exempt never edited, never event'd

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

        # FR-4/FR-5/FR-6: revise the synthesis region + one `observed` event.
        outcomes.append(_edit_page(path, T, score, synth_body_fn))
    return outcomes


def _edit_page(path, trigger, score, synth_body_fn):
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return {"path": path, "action": "unreadable"}

    provenance = _provenance_line(trigger, score)
    region_body = synth_body_fn(trigger, {"slug": _slug_of(path)}, provenance)

    # FR-8 idempotence: deterministic body → if the current region already equals
    # what we would write, skip the edit AND the event (append_event stamps a fresh
    # ts, so the PK cannot dedup — this explicit content guard must).
    cur = current_region_body(content)
    if cur is not None and cur.strip() == region_body.rstrip():
        return {"path": path, "action": "idempotent_skip"}

    new_content, rid, op = apply_region(content, region_body)
    if new_content == content:
        return {"path": path, "action": "idempotent_skip"}
    _atomic_write(path, new_content)
    # FR-6 NO-LAUNDER: at most ONE tier-1 `observed` (+0.05, capped) — never
    # confirmed/verified_by_test. Provably cannot lift a page across the 0.55 gate.
    _EV.append_event(path, "observed", actor="agent-extracted",
                     note=f"m2 synthesis from {trigger['slug']}")
    _oplog("m2-synthesize", _slug_of(path),
           f"region {op} from trigger={trigger['slug']} score={score:.3f}")
    return {"path": path, "action": "edited", "op": op, "region_id": rid}


# --- ingest hook (FR-1/FR-9) -------------------------------------------------
def run_on_ingest(conn, index_db_path, changed_paths, confirmer=None, supersedes=None):
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
                                confirmer=confirmer, supersedes=supersedes)
        except Exception as e:
            print(f"WARN: M2 skipped {path}: {e}", file=sys.stderr)
