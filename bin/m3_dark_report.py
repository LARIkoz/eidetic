#!/usr/bin/env python3
"""FR-5 — the acquisition dark-run report (the D5 gate's instrument).

Reads `events/m3_acquisition_dark.jsonl` + `events/m3_driver.log` (+
`events/m3_filed.jsonl`) and prints the owner-eyeball report, four sections:

  1. WOULD-FILE MARKING SHEET — deduped by the FR-8 candidate key, then
     paraphrase-clustered (the cache keys EXACT normalized text, so the same
     knowledge re-worded on a later Stop logs again); one block per knowledge
     item (claim + quote + re-wordings) with keep / noise / dangerous-wrong
     checkboxes for the owner.
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
import m3_judge as _judge  # noqa: E402  (_norm_tokens — the one dialect, NFR-6)
import m3_recall_miner as _miner  # noqa: E402
import m3_seen_cache as _cache  # noqa: E402

# Live-data calibration 2026-07-11 (104 dark rows / 69 would-file): at 0.4 all
# 12 multi-clusters are true paraphrases; 0.5 already splits known re-wordings
# (en-CA-primary ×3) — so 0.4, the stricter of the two equivalent settings.
CLUSTER_JACCARD = 0.4


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


def _claim_tokens(row):
    return set(_judge._norm_tokens(row.get("claim") or "").split())


def _cluster_paraphrases(rows, threshold=CLUSTER_JACCARD):
    """Group rows whose claims are the same knowledge re-worded — the FR-8 key
    is exact-text, so paraphrases slip past it (observed live: en-CA-primary
    ×3, T-A ×3). Representative-linkage on claim-token Jaccard, within kind
    only (kind is part of the candidate key's identity). Report-side ONLY —
    gates and the seen-cache are untouched (V1 fix 1)."""
    clusters = []
    for r in rows:
        toks = _claim_tokens(r)
        for c in clusters:
            rep = c[0]
            if rep.get("kind") != r.get("kind"):
                continue
            rep_toks = _claim_tokens(rep)
            union = toks | rep_toks
            if union and len(toks & rep_toks) / len(union) >= threshold:
                c.append(r)
                break
        else:
            clusters.append([r])
    return clusters


def section_sheet(dark_rows):
    uniq, _ = _dedup_dark(dark_rows)
    would = [r for r in uniq if r.get("would_file")]
    clusters = _cluster_paraphrases(would)
    lines = [f"## 1. Would-file marking sheet — {len(clusters)} knowledge items "
             f"({len(would)} would-file rows, dedup'd from {len(dark_rows)} dark "
             f"rows; paraphrases clustered at claim-token Jaccard ≥ "
             f"{CLUSTER_JACCARD})", ""]
    for i, c in enumerate(clusters, 1):
        r = c[0]
        sessions = []
        for m in c:
            s = (m.get("session_id") or "")[:12]
            if s and s not in sessions:
                sessions.append(s)
        head = (f"### {i}. [{r.get('kind')}] session={','.join(sessions)} "
                f"project={r.get('project_slug') or '-'}")
        if len(c) > 1:
            head += f" ×{len(c)} re-worded"
        lines += [head,
                  f"CLAIM: {r.get('claim')}",
                  f"QUOTE: {r.get('transcript_quote')}"]
        lines += [f"  ALSO: {m.get('claim')}" for m in c[1:]]
        lines += ["MARK:  [ ] keep   [ ] noise   [ ] dangerous-wrong", ""]
    if not clusters:
        lines.append("(no would-file rows yet)")
    return "\n".join(lines)


def section_counters(runs, filed_rows):
    """Per-kind metrics are computed over V3 runs only (rows whose meta carries
    raw_by_kind); pre-v3 driver rows lack the fields and would dilute the
    candidates/session and page-rate the D5 gate reads. Legacy rows are shown
    as the baseline they are."""
    v3_runs = [r for r in runs if (r.get("meta") or {}).get("raw_by_kind") is not None]
    legacy = [r for r in runs if r not in v3_runs]
    raw, kept = {}, {}
    pages = 0
    skipped_seen = 0
    tally_total = {}
    for r in v3_runs:
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
    n = len(v3_runs)
    kept_total = sum(kept.values())
    legacy_kept = sum((r.get("meta") or {}).get("kept") or 0 for r in legacy)
    legacy_pages = sum(
        1 for r in legacy
        if ((r.get("tally") or {}).get("filed", 0)
            + (r.get("tally") or {}).get("deduped_to_m2", 0)) > 0)
    lines = [
        "## 2. Consolidation counters (per kind, v3 runs only)",
        "",
        f"v3 runs: {n} · legacy (pre-v3) runs: {len(legacy)}",
        f"raw by kind:  {json.dumps(raw, ensure_ascii=False)}",
        f"kept by kind: {json.dumps(kept, ensure_ascii=False)}",
        f"actions:      {json.dumps(tally_total, ensure_ascii=False)}",
        f"skipped_seen (FR-8 cache hits): {skipped_seen}",
        "",
        (f"candidates/session (v3): {kept_total / n:.2f} (target ≥ 2.0 = 2x v2 baseline)"
         if n else "candidates/session (v3): n/a (no v3 runs yet)"),
        (f"sessions producing a page (filed ∪ deduped_to_m2, v3): {pages}/{n}"
         f" = {100.0 * pages / n:.1f}% (target ≥ 10%)"
         if n else "page-rate (v3): n/a"),
        (f"legacy baseline: {legacy_kept / len(legacy):.2f} cand/session, "
         f"page-rate {100.0 * legacy_pages / len(legacy):.1f}%"
         if legacy else "legacy baseline: none"),
        f"filed pages total (m3_filed.jsonl): {len(filed_rows)}",
    ]
    return "\n".join(lines)


def _coverage_probe(transcript, use_judge):
    """Re-mine turns −60..−30 of ONE transcript with the live miner prompt;
    → (had_prev_window, would-file-worthy count). Shared by both lanes —
    only path RESOLUTION differs (NFR-6)."""
    turns60 = _miner.read_turns(transcript, max_turns=60)
    prev_window = turns60[:max(0, len(turns60) - 30)]
    if not prev_window:
        return False, 0
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
        return True, passed
    finally:
        os.unlink(tmp_path)


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
        had, passed = _coverage_probe(matches[0], use_judge)
        if not had:
            continue
        inside += 1
        outside += passed
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


# --- FR-5 agent lane (spec-m3-agent-lane-plumbing) ---------------------------
# Read-side D3 purity: --lane agent reads m3_agent_dark.jsonl + the agent
# ledger + driver agent blocks (+ main dark rows for D8 comparison only);
# the default --lane main path is byte-compatible with the pre-change report.


def _row_definitive(r):
    """Definitive dark row: entailed (would_file), judged not_entailed, or a
    quote-gate reject (judge null + quote_ok false). judge_unavailable (or
    anything else) is transient — health evidence, never a gate item."""
    if r.get("would_file"):
        return True
    if r.get("judge") == "not_entailed":
        return True
    if not r.get("quote_ok") and r.get("judge") is None:
        return True
    return False


def _resolve_attempts(rows):
    """Outcome-aware attempt resolution (FR-5): group retries/concurrent
    attempts by (project_slug, agent_file, candidate_key); a LATER definitive
    outcome outranks earlier transients; conflicting definitive outcomes are
    unresolved — loud and gate-excluded, never silently \"keep first\".
    → (resolved rows, health {transient_only, dup_attempts, conflicts})."""
    groups, order = {}, []
    for r in rows:
        key = (r.get("project_slug") or "", r.get("agent_file") or "",
               _cache.candidate_key(r))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)
    resolved, conflicts = [], []
    transient_only = dup_attempts = 0
    for key in order:
        g = groups[key]
        dup_attempts += len(g) - 1
        defs = [r for r in g if _row_definitive(r)]
        if not defs:
            transient_only += 1
            continue
        if len({bool(r.get("would_file")) for r in defs}) > 1:
            conflicts.append(defs[-1])
            continue
        resolved.append(defs[-1])
    return resolved, {"transient_only": transient_only,
                      "dup_attempts": dup_attempts, "conflicts": conflicts}


def _tag_seen_main(clusters, main_rows):
    """D8 report-time cross-lane accounting: an agent cluster whose claim
    clusters with ANY main-lane dark claim (same kind, same Jaccard) is
    already_seen_main — one item in the QUALITY denominator, zero in the
    incremental/net-new counters. → [bool] aligned with clusters."""
    main_by_kind = {}
    for r in main_rows:
        main_by_kind.setdefault(r.get("kind"), []).append(_claim_tokens(r))
    tags = []
    for c in clusters:
        rep = c[0]
        toks = _claim_tokens(rep)
        seen = False
        for mt in main_by_kind.get(rep.get("kind"), []):
            union = toks | mt
            if union and len(toks & mt) / len(union) >= CLUSTER_JACCARD:
                seen = True
                break
        tags.append(seen)
    return tags


def section_agent_sheet(resolved, main_rows, total_rows):
    """Marking sheet on RESOLVED rows: FR-8-key dedup happened in resolution;
    paraphrase clustering reused; grouped by project_slug + agent_kind; each
    item shows claim + quote + re-wordings + transcript_mtime (the found-at
    lifecycle anchor). → (text, clusters, seen_main tags)."""
    would = [r for r in resolved if r.get("would_file")]
    clusters = _cluster_paraphrases(would)
    tags = _tag_seen_main(clusters, main_rows)
    lines = [f"## 1. Agent would-file marking sheet — {len(clusters)} knowledge "
             f"items ({len(would)} resolved would-file rows from {total_rows} "
             f"agent dark rows; paraphrases clustered at claim-token Jaccard ≥ "
             f"{CLUSTER_JACCARD})", ""]
    order = sorted(range(len(clusters)),
                   key=lambda i: (clusters[i][0].get("project_slug") or "",
                                  clusters[i][0].get("agent_kind") or ""))
    for n, i in enumerate(order, 1):
        c = clusters[i]
        r = c[0]
        head = (f"### {n}. [{r.get('kind')}] "
                f"project={r.get('project_slug') or '-'} "
                f"agent_kind={r.get('agent_kind') or '-'} "
                f"session={(r.get('session_id') or '')[:24]} "
                f"mtime={r.get('transcript_mtime') or '-'}")
        if len(c) > 1:
            head += f" ×{len(c)} re-worded"
        if tags[i]:
            head += "  [already_seen_main]"
        lines += [head,
                  f"CLAIM: {r.get('claim')}",
                  f"QUOTE: {r.get('transcript_quote')}"]
        lines += [f"  ALSO: {m.get('claim')}" for m in c[1:]]
        lines += ["MARK:  [ ] keep   [ ] noise   [ ] dangerous-wrong", ""]
    if not clusters:
        lines.append("(no resolved would-file rows yet)")
    return "\n".join(lines), clusters, tags


def section_agent_counters(agent_runs, resolved, done_rows, dark_rows,
                           clusters, tags):
    """Lane counters (FR-5): scanned/eligible/mined/recall-dropped/judge
    calls SUMMED across per-fire agent blocks; backlog depth + oldest age
    from the LATEST snapshot per project (sum depth, max age); density =
    unique clustered would-file items per mined agent-session (the V5 bar
    re-check at the production cap); accumulation bar = distinct parent
    sessions with ≥1 definitive row."""
    sums = {k: 0 for k in ("scanned", "eligible", "mined_files",
                           "recall_dropped", "judge_calls", "skipped_seen")}
    latest_by_project = {}
    infra = {}
    for b in agent_runs:
        for k in sums:
            sums[k] += b.get(k) or 0
        for k, v in (b.get("tally") or {}).items():
            # Review P2-7: infra failures must not masquerade as poor yield.
            if k in ("miner_error", "missing", "error", "dark_append_failed",
                     "cache_record_failed", "ledger_append_failed"):
                infra[k] = infra.get(k, 0) + v
        slug = b.get("project_slug") or "?"
        prev = latest_by_project.get(slug)
        # Review P2-5: concurrent drains can append out of order — the latest
        # SNAPSHOT is the max agent-block ts, not the last JSONL row.
        if prev is None or (b.get("ts") or "") >= (prev.get("ts") or ""):
            latest_by_project[slug] = b
    backlog_total = sum((b.get("backlog") or 0)
                        for b in latest_by_project.values())
    oldest_max = max([b.get("oldest_eligible_s") or 0
                      for b in latest_by_project.values()] or [0])
    per = {}
    for r in resolved:
        if not r.get("would_file"):
            continue
        key = (f"{r.get('kind')}/{r.get('agent_kind') or '-'}"
               f"/{r.get('project_slug') or '-'}")
        per[key] = per.get(key, 0) + 1
    # Review P1-4: canonical (project_slug, agent_file) identity — a bare
    # relpath would merge same-relpath sessions across projects.
    mined_sessions = {(r.get("project_slug") or "", r.get("agent_file"))
                      for r in dark_rows if r.get("agent_file")}
    mined_sessions |= {(d.get("project_slug") or "", d.get("agent_file"))
                       for d in done_rows if d.get("agent_file")}
    net_new = sum(1 for t in tags if not t)
    # Review P1-4 / D8: already_seen_main clusters stay in the QUALITY
    # denominator but are excluded from incremental density — the V5 bar is
    # recomputed on NET-NEW items only.
    density = (net_new / len(mined_sessions)) if mined_sessions else 0.0
    parents = {r.get("parent_session_id") for r in resolved
               if r.get("parent_session_id")}
    return "\n".join([
        "## 2. Agent lane counters",
        "",
        f"fires (agent blocks): {len(agent_runs)}",
        f"files scanned: {sums['scanned']} · eligible: {sums['eligible']} · "
        f"mined: {sums['mined_files']}",
        f"backlog depth (latest per project, summed): {backlog_total} · "
        f"oldest eligible age: {oldest_max}s",
        f"recall_dropped: {sums['recall_dropped']} · judge calls: "
        f"{sums['judge_calls']} · skipped_seen (FR-8): {sums['skipped_seen']}",
        f"infra health (file-level failures across fires): "
        f"{json.dumps(infra, ensure_ascii=False, sort_keys=True)}",
        f"would-file per kind/agent_kind/project: "
        f"{json.dumps(per, ensure_ascii=False, sort_keys=True)}",
        f"knowledge items (clustered): {len(clusters)} · net-new vs main: "
        f"{net_new} · already_seen_main: {len(clusters) - net_new}",
        f"NET-NEW would-file per mined agent-session (cap "
        f"{_miner.MAX_CANDIDATES}): {density:.2f} — V5 bar ≥ 0.5 feeds "
        f"kill/iterate regardless of keep-rate (D8: already_seen_main "
        f"excluded from the numerator)",
        f"distinct parent sessions with ≥1 definitive agent row: "
        f"{len(parents)} (accumulation bar: ≥ 20)",
    ])


def section_agent_coverage(dark_rows, done_rows, sample, use_judge):
    """Window coverage over AGENT transcripts: resolve each nested transcript
    from project_slug + agent_file (ledger join allowed — done rows carry
    both), covering plain and workflow layouts; skip-and-say-so otherwise."""
    lines = [f"## 3. Window coverage sample (turns -60..-30, "
             f"{'judge ON' if use_judge else 'mechanical gate only'})", ""]
    seen_files = {}
    for r in list(done_rows) + list(dark_rows):
        slug, af = r.get("project_slug"), r.get("agent_file")
        if slug and af:
            seen_files.setdefault((slug, af), None)
    pairs = list(seen_files)[-sample:] if sample else []
    if not pairs:
        lines.append("(skipped — no agent files sampled)")
        return "\n".join(lines)
    outside = missing = plain = wf = 0
    for slug, af in pairs:
        path = os.path.expanduser(f"~/.claude/projects/{slug}/{af}")
        if not os.path.isfile(path):
            missing += 1
            continue
        if any(p.startswith("wf_") for p in af.split("/")):
            wf += 1
        else:
            plain += 1
        had, passed = _coverage_probe(path, use_judge)
        if had:
            outside += passed
    lines += [
        f"agent files sampled: {len(pairs)} (plain: {plain} · workflow: {wf} "
        f"· missing: {missing})",
        f"would-file-worthy candidates found OUTSIDE the 30-turn window: "
        f"{outside}",
        "verdict: widen the window ONLY if this number says so.",
    ]
    return "\n".join(lines)


def section_agent_dups(health, tags):
    """Dup & health visibility (D8 explicit, not decorative): duplicate
    attempts by key, transient-only keys, cross-lane restatements, and
    conflicting definitive outcomes (loud, gate-excluded)."""
    lines = ["## 4. Dup & health visibility", "",
             f"duplicate attempts collapsed by (scope, key): "
             f"{health['dup_attempts']}",
             f"transient-only keys (health counters, never gate items): "
             f"{health['transient_only']}",
             f"cross-lane restatements (already_seen_main clusters): "
             f"{sum(1 for t in tags if t)}",
             f"CONFLICTING definitive outcomes — gate-EXCLUDED until "
             f"adjudicated: {len(health['conflicts'])}"]
    lines += [f"  ! [{r.get('kind')}] {r.get('project_slug')}"
              f"/{r.get('agent_file')}: {(r.get('claim') or '')[:100]}"
              for r in health["conflicts"]]
    return "\n".join(lines)


class LedgerStateLost(RuntimeError):
    """Agent-lane ledger failed D6 validation — no gate data may be derived."""


def collect_lane_items(ms, lane):
    """Machine-readable clustered would-file items for a lane — the ONE
    collection path shared by the text report, `--json`, and the commission
    (brief-m3-commission-gate). → (items, meta).

    item = {key, lane, kind, claim, quote, rewordings, project_slug,
    session_id, agent_kind, workflow_id, transcript_mtime, seen_main}.
    `key` = the FR-8 candidate key of the cluster representative — stable
    across runs, so commission verdicts can resume against it. Agent lane
    inherits D6 fail-closed: state_lost raises LedgerStateLost (P1-3
    discipline on every read path)."""
    events = os.path.join(ms, "events")
    if lane == "agent":
        import m3_agent_lane as _lane
        _floor, _done, status = _lane.read_ledger(ms)
        if status != "ok":
            raise LedgerStateLost(
                "agent ledger state_lost — repair before any gate work")
        ledger_rows = _read_jsonl(os.path.join(events, _lane.LEDGER_FILE))
        init_row = next(r for r in ledger_rows if r.get("type") == "init")
        dark = _read_jsonl(os.path.join(events, _lane.DARK_FILE))
        window_start = init_row.get("ts") or ""
        if window_start:
            dark = [r for r in dark if (r.get("ts") or "") >= window_start]
        resolved, health = _resolve_attempts(dark)
        would = [r for r in resolved if r.get("would_file")]
        clusters = _cluster_paraphrases(would)
        main_rows = _read_jsonl(os.path.join(events, _acq.DARK_FILE))
        tags = _tag_seen_main(clusters, main_rows)
        meta = {"lane": lane, "dark_rows": len(dark),
                "resolved": len(resolved), "conflicts":
                len(health["conflicts"]), "transient_only":
                health["transient_only"]}
    else:
        dark = _read_jsonl(os.path.join(events, _acq.DARK_FILE))
        uniq, _dups = _dedup_dark(dark)
        would = [r for r in uniq if r.get("would_file")]
        clusters = _cluster_paraphrases(would)
        tags = [False] * len(clusters)  # cross-lane tagging is agent-side only
        meta = {"lane": lane, "dark_rows": len(dark), "resolved": len(uniq)}
    items = []
    for c, seen in zip(clusters, tags):
        r = c[0]
        items.append({
            "key": _cache.candidate_key(r),
            "lane": lane,
            "kind": r.get("kind"),
            "claim": r.get("claim"),
            "quote": r.get("transcript_quote"),
            "rewordings": [m.get("claim") for m in c[1:]],
            "project_slug": r.get("project_slug"),
            "session_id": r.get("session_id"),
            "agent_kind": r.get("agent_kind"),
            "workflow_id": r.get("workflow_id"),
            "transcript_mtime": r.get("transcript_mtime"),
            "seen_main": bool(seen),
        })
    meta["items"] = len(items)
    return items, meta


def _run_agent_lane(ms, events, args):
    import m3_agent_lane as _lane
    dark = _read_jsonl(os.path.join(events, _lane.DARK_FILE))
    ledger_rows = _read_jsonl(os.path.join(events, _lane.LEDGER_FILE))
    done_rows = [r for r in ledger_rows if r.get("type") == "done"]
    # Review P1-3: the report inherits D6 fail-closed via the SAME runtime
    # validator (one fact, one place) — a duplicate/non-first/malformed init
    # must not silently produce gate metrics.
    _floor_ns, _done_map, ledger_status = _lane.read_ledger(ms)
    print(f"# M3 AGENT-lane dark-run report — {ms}")
    if ledger_status != "ok":
        print("!! LEDGER STATE LOST (missing/duplicate/non-first/malformed "
              "init) — gate rendering SUPPRESSED (D6 fail-closed). Repair "
              "the ledger from preserved evidence before reading any gate "
              "numbers.")
        print(f"raw rows on disk: agent dark {len(dark)} · ledger "
              f"{len(ledger_rows)} (done {len(done_rows)})")
        return
    init_row = next(r for r in ledger_rows if r.get("type") == "init")
    main_dark = _read_jsonl(os.path.join(events, _acq.DARK_FILE))
    agent_runs = [r["agent"] for r in _driver_runs(events)
                  if isinstance(r.get("agent"), dict)]
    window_start = init_row.get("ts") or ""
    if window_start:  # the agent window starts at the authoritative init
        dark = [r for r in dark if (r.get("ts") or "") >= window_start]
        agent_runs = [b for b in agent_runs
                      if (b.get("ts") or "") >= window_start]
    resolved, health = _resolve_attempts(dark)

    print(f"window start (init floor): "
          f"{init_row.get('mtime_floor') or init_row.get('ts')}")
    print(f"agent dark rows: {len(dark)} · agent fires: {len(agent_runs)} · "
          f"done files: {len(done_rows)}")
    print()
    sheet, clusters, tags = section_agent_sheet(resolved, main_dark, len(dark))
    print(sheet)
    print()
    print(section_agent_counters(agent_runs, resolved, done_rows, dark,
                                 clusters, tags))
    print()
    print(section_agent_coverage(dark, done_rows, args.coverage, args.judge))
    print()
    print(section_agent_dups(health, tags))
    print()
    print("Agent-D5 gate (D4; dangerous-wrong budget frozen at the spec gate, "
          "default (a) ≤1/round): activate ≥70% keep AND budget held · kill "
          "<50% keep · between → iterate. Bar: ≥2–4 days AND ≥20 distinct "
          "parent sessions with definitive rows. Owner verdict recorded "
          "BEFORE any activation work (spec §5).")
    print("NB: this report contains verbatim claims/quotes — local material, "
          "never commit it (NFR-4).")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-system", default=None)
    ap.add_argument("--coverage", type=int, default=10,
                    help="sessions for the window-coverage sample (0 = skip)")
    ap.add_argument("--judge", action="store_true",
                    help="entail coverage-sample candidates (≤4 calls/session)")
    ap.add_argument("--lane", choices=("main", "agent"), default="main",
                    help="agent = the agent-lane report (FR-5; default main "
                         "stays byte-compatible with today)")
    ap.add_argument("--json", action="store_true",
                    help="print the lane's clustered would-file items as "
                         "JSON (the commission's input) instead of the "
                         "human report")
    args = ap.parse_args()

    ms = _ms_root(args.memory_system)
    events = os.path.join(ms, "events")

    if args.json:
        items, meta = collect_lane_items(ms, args.lane)
        print(json.dumps({"meta": meta, "items": items}, ensure_ascii=False))
        return

    if args.lane == "agent":
        _run_agent_lane(ms, events, args)
        return
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
