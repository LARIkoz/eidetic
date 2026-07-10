#!/usr/bin/env python3
"""FR-5 — the acquisition dark-run report (the D5 gate's instrument).

Reads `events/m3_acquisition_dark.jsonl` + `events/m3_driver.log` (+
`events/m3_filed.jsonl`) and prints the owner-eyeball report, four sections:

  1. WOULD-FILE MARKING SHEET — deduped by the FR-8 candidate key; one block
     per would-file card (claim + quote + session) with keep / noise /
     dangerous-wrong checkboxes for the owner.
  2. CONSOLIDATION COUNTERS, per kind — the shared 4-slot cap lets acquisition
     displace recall candidates, so totals are attributed per kind; "producing
     a page" counts filed ∪ deduped_to_m2 (else the FR-6 door suppresses the
     very metric it shares a deploy with).
  3. WINDOW COVERAGE SAMPLE — re-mine turns −60..−30 of recent dark-run
     sessions; what fraction of would-file knowledge sits OUTSIDE the 30-turn
     window (widen only if this number says so). Mechanical quote gate by
     default; --judge adds entailment (bounded: ≤4 calls/session).
  4. DUP VISIBILITY — duplicate dark rows by key, plus near-duplicate FILED
     pairs among new pages (slug-token Jaccard) — keeps the FR-6 cost measured
     even if its fix slips a deploy.

The rendered report (verbatim claims + quotes) is LOCAL material — never
commit it anywhere (NFR-4); only aggregate numbers may travel.

D5 gate (owner decides): activate at ≥70%% keep AND ≤1 dangerous-wrong (that
one must be caught by M2 suggestion); kill at <50%% keep; between → iterate
the prompt, run another dark round.
"""
import argparse
import glob
import json
import os
import sys

_BIN = os.path.dirname(os.path.abspath(__file__))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)

import m3_acquisition as _acq  # noqa: E402
import m3_recall_miner as _miner  # noqa: E402
import m3_seen_cache as _cache  # noqa: E402


def _ms_root(arg):
    return arg or os.environ.get(
        "EIDETIC_MEMORY_SYSTEM", os.path.expanduser("~/.claude/memory-system"))


def _read_jsonl(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def _driver_runs(events_dir):
    """m3_driver.log holds one JSON line per run (plus stray SDK noise lines —
    skipped). Returns only 'ran' rows."""
    return [r for r in _read_jsonl(os.path.join(events_dir, "m3_driver.log"))
            if r.get("m3_driver") == "ran"]


def _dedup_dark(rows):
    """Dedup dark rows by the FR-8 candidate key; keep the FIRST verdict row
    per key (later re-judges of the same candidate add no information)."""
    seen, out, dups = set(), [], 0
    for r in rows:
        key = _cache.candidate_key(r)
        if key in seen:
            dups += 1
            continue
        seen.add(key)
        out.append(r)
    return out, dups


def section_sheet(dark_rows):
    uniq, _ = _dedup_dark(dark_rows)
    would = [r for r in uniq if r.get("would_file")]
    lines = [f"## 1. Would-file marking sheet — {len(would)} cards "
             f"(dedup'd from {len(dark_rows)} dark rows)", ""]
    for i, r in enumerate(would, 1):
        lines += [
            f"### {i}. [{r.get('kind')}] session={r.get('session_id', '')[:12]} "
            f"project={r.get('project_slug') or '-'}",
            f"CLAIM: {r.get('claim')}",
            f"QUOTE: {r.get('transcript_quote')}",
            "MARK:  [ ] keep   [ ] noise   [ ] dangerous-wrong",
            "",
        ]
    if not would:
        lines.append("(no would-file rows yet)")
    return "\n".join(lines)


def section_counters(runs, filed_rows):
    n = len(runs)
    raw, kept = {}, {}
    pages = 0
    skipped_seen = 0
    tally_total = {}
    for r in runs:
        meta = r.get("meta") or {}
        for k, v in (meta.get("raw_by_kind") or {}).items():
            raw[k] = raw.get(k, 0) + v
        for k, v in (meta.get("kept_by_kind") or {}).items():
            kept[k] = kept.get(k, 0) + v
        skipped_seen += meta.get("skipped_seen") or 0
        t = r.get("tally") or {}
        for k, v in t.items():
            tally_total[k] = tally_total.get(k, 0) + v
        if (t.get("filed", 0) + t.get("deduped_to_m2", 0)) > 0:
            pages += 1
    kept_total = sum(kept.values())
    lines = [
        "## 2. Consolidation counters (per kind)",
        "",
        f"runs (ran): {n}",
        f"raw by kind:  {json.dumps(raw, ensure_ascii=False)}",
        f"kept by kind: {json.dumps(kept, ensure_ascii=False)}",
        f"actions:      {json.dumps(tally_total, ensure_ascii=False)}",
        f"skipped_seen (FR-8 cache hits): {skipped_seen}",
        "",
        f"candidates/session: {kept_total / n:.2f} (target ≥ 2.0 = 2x v2 baseline ~1.0)"
        if n else "candidates/session: n/a (no runs)",
        f"sessions producing a page (filed ∪ deduped_to_m2): {pages}/{n}"
        f" = {100.0 * pages / n:.1f}%% (target ≥ 10%%, v2 baseline 5%%)"
        if n else "page-rate: n/a",
        f"filed pages total (m3_filed.jsonl): {len(filed_rows)}",
    ]
    return "\n".join(lines)


def section_coverage(dark_rows, sample, use_judge):
    """Re-mine turns −60..−30 of the most recent dark-run sessions whose
    transcripts still exist; count acquisition candidates that pass the
    mechanical quote gate there (i.e. knowledge OUTSIDE the shipped window)."""
    lines = [f"## 3. Window coverage sample (turns -60..-30, "
             f"{'judge ON' if use_judge else 'mechanical gate only'})", ""]
    by_session = {}
    for r in dark_rows:
        sid = r.get("session_id")
        if sid:
            by_session.setdefault(sid, r)
    sessions = list(by_session)[-sample:] if sample else []
    if not sessions:
        lines.append("(skipped — no sessions sampled)")
        return "\n".join(lines)
    inside = outside = missing = 0
    for sid in sessions:
        matches = glob.glob(os.path.expanduser(
            f"~/.claude/projects/*/{sid}.jsonl"))
        if not matches:
            missing += 1
            continue
        transcript = matches[0]
        turns60 = _miner.read_turns(transcript, max_turns=60)
        prev_window = turns60[:max(0, len(turns60) - 30)]
        if not prev_window:
            continue
        inside += 1
        # Mine the PRECEDING window with the live miner prompt.
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                         encoding="utf-8") as tf:
            for role, text in prev_window:
                tf.write(json.dumps({"type": role, "message": {
                    "content": [{"type": "text", "text": text}]}},
                    ensure_ascii=False) + "\n")
            tmp_path = tf.name
        try:
            cands, _meta = _miner.mine_transcript(tmp_path)
            acq_cands = [c for c in cands if c.get("kind") in _miner.ACQ_KINDS]
            passed = 0
            for c in acq_cands:
                if _acq.quote_in_assistant_turns(
                        c.get("transcript_quote"), prev_window):
                    if not use_judge:
                        passed += 1
                    else:
                        import m3_judge
                        if m3_judge.verdict(c.get("claim"),
                                            [c.get("transcript_quote")]) == "entailed":
                            passed += 1
            outside += passed
        finally:
            os.unlink(tmp_path)
    lines += [
        f"sessions sampled: {len(sessions)} (transcript missing: {missing})",
        f"would-file-worthy candidates found OUTSIDE the 30-turn window: {outside}",
        "verdict: widen the window ONLY if this number says so (spec §3 note).",
    ]
    return "\n".join(lines)


def _slug_tokens(slug):
    return set(t for t in (slug or "").replace("synthesis-", "").split("-")
               if len(t) > 2)


def section_dups(dark_rows, filed_rows):
    _, dup_rows = _dedup_dark(dark_rows)
    pairs = []
    for i, a in enumerate(filed_rows):
        for b in filed_rows[i + 1:]:
            if a.get("project_slug") != b.get("project_slug"):
                continue
            ta, tb = _slug_tokens(a.get("filed_slug")), _slug_tokens(b.get("filed_slug"))
            if not ta or not tb:
                continue
            j = len(ta & tb) / len(ta | tb)
            if j >= 0.6:
                pairs.append((a.get("filed_slug"), b.get("filed_slug"), round(j, 2)))
    lines = ["## 4. Dup visibility", "",
             f"duplicate dark-log rows suppressed by key-dedup: {dup_rows}",
             f"near-duplicate FILED pairs (slug Jaccard ≥ 0.6): {len(pairs)}"]
    lines += [f"  - {a} ~ {b} ({j})" for a, b, j in pairs]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-system", default=None)
    ap.add_argument("--coverage", type=int, default=10,
                    help="sessions for the window-coverage sample (0 = skip)")
    ap.add_argument("--judge", action="store_true",
                    help="entail coverage-sample candidates (≤4 calls/session)")
    args = ap.parse_args()

    ms = _ms_root(args.memory_system)
    events = os.path.join(ms, "events")
    dark = _read_jsonl(os.path.join(events, _acq.DARK_FILE))
    runs = _driver_runs(events)
    filed = _read_jsonl(os.path.join(events, "m3_filed.jsonl"))

    print(f"# M3 acquisition dark-run report — {ms}")
    print(f"dark rows: {len(dark)} · driver runs: {len(runs)} · filed: {len(filed)}")
    print()
    print(section_sheet(dark))
    print()
    print(section_counters(runs, filed))
    print()
    print(section_coverage(dark, args.coverage, args.judge))
    print()
    print(section_dups(dark, filed))
    print()
    print("D5 gate: activate ≥70% keep AND ≤1 dangerous-wrong (M2 must catch it) · "
          "kill <50% keep · else iterate. Owner verdict required before any "
          "activation work (spec §5).")
    print("NB: this report contains verbatim claims/quotes — local material, "
          "never commit it (NFR-4).")


if __name__ == "__main__":
    main()
