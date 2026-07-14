#!/usr/bin/env python3
"""Candidate-first salvage of immutable M3 legacy replay miner caches.

This tool deliberately does not map a legacy derived row to a freshly mined
candidate.  It treats the current-miner candidate itself as the review unit,
while preserving a body-free manifest that binds every candidate to the
immutable legacy manifest, miner cache, transcript hash, and policy identity.

There is no production-write or import mode.  Judge calls are explicit,
bounded, sequential, and write only to a new replay namespace.
"""
import argparse
import json
import os
import re
import stat
from collections import Counter, defaultdict

_BIN = os.path.dirname(os.path.abspath(__file__))

import m3_legacy_replay as legacy


INVENTORY_SCHEMA = "m3-legacy-candidate-inventory-v1"
RESULT_SCHEMA = "m3-legacy-candidate-result-v1"
CHECKPOINT_SCHEMA = "m3-legacy-candidate-checkpoint-v1"
BINDING_SCHEMA = "m3-legacy-candidate-binding-v1"
SUMMARY_SCHEMA = "m3-legacy-candidate-summary-v1"
POLICY = "m3-legacy-candidate-replay-v1"
MAX_WORKERS = 1
TERMINAL_OUTCOMES = frozenset({
    "rejected_noise", "invalid_evidence", "conflict_manual_review",
    "review_candidate",
})
ALL_OUTCOMES = TERMINAL_OUTCOMES | {"transient_retry"}
_CORRECTION_RE = re.compile(
    r"(?:\bcorrection\b|\bcorrected\b|\bactually\b|\bsupersed(?:e|ed)\b|"
    r"\bисправ\w*\b|\bпоправ\w*\b|\bна самом деле\b|\bотмен\w*\b)",
    re.IGNORECASE,
)


class CandidateReplayError(legacy.ReplayError):
    """A fail-closed candidate replay contract violation."""


def _load_source_legacy_manifest(path):
    """Load the frozen v3 manifest while allowing historical judge code.

    Candidate extraction and the deterministic durability rail belong to the
    miner, so that implementation must still match byte-for-byte.  The source
    judge is evidence already frozen in replay-results.jsonl and may differ
    from today's judge implementation; requiring its current hash would make a
    valid immutable replay unreadable after an unrelated judge refactor.
    """
    manifest = legacy._load_manifest(path, require_current_policy=False)
    identity = manifest.get("policy_identity") or {}
    current = legacy._policy_identity()
    if identity.get("replay_policy") != legacy.REPLAY_POLICY:
        raise CandidateReplayError("source legacy replay policy is unsupported")
    if identity.get("miner_policy") != legacy._miner.MINER_POLICY_VERSION:
        raise CandidateReplayError("source legacy miner policy differs")
    source_miner = (identity.get("files") or {}).get("m3_recall_miner.py")
    current_miner = (current.get("files") or {}).get("m3_recall_miner.py")
    if not source_miner or source_miner != current_miner:
        raise CandidateReplayError("source legacy miner implementation differs")
    return manifest


def _policy_identity():
    files = {}
    for name in ("m3_legacy_candidate_replay.py", "m3_legacy_replay.py",
                 "m3_recall_miner.py", "m3_judge.py"):
        files[name] = legacy._sha_file(os.path.join(_BIN, name))
    return {
        "candidate_replay_policy": POLICY,
        "miner_policy": legacy._miner.MINER_POLICY_VERSION,
        "files": files,
    }


def _load_json(path, label):
    legacy._reject_symlink(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise CandidateReplayError("%s is unreadable" % label) from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise CandidateReplayError("%s is not a regular file" % label)
        with os.fdopen(fd, encoding="utf-8") as fh:
            fd = -1
            value = json.load(fh)
    except CandidateReplayError:
        raise
    except Exception as exc:
        raise CandidateReplayError("%s is unreadable" % label) from exc
    finally:
        if fd >= 0:
            os.close(fd)
    if not isinstance(value, dict):
        raise CandidateReplayError("%s must be a JSON object" % label)
    return value


def _candidate_compact(candidate):
    return {
        "kind": str(candidate.get("kind") or ""),
        "claim": str(candidate.get("claim") or ""),
        "transcript_quote": str(candidate.get("transcript_quote") or ""),
        "miner_policy": str(candidate.get("miner_policy") or
                            legacy._miner.MINER_POLICY_VERSION),
    }


def _candidate_id(transcript_sha256, candidate):
    compact = _candidate_compact(candidate)
    return legacy._sha_bytes(legacy._canonical({
        "candidate_replay_policy": POLICY,
        "transcript_sha256": transcript_sha256,
        "candidate": compact,
    }).encode("utf-8"))


def _candidate_body_sha(candidate):
    return legacy._sha_bytes(
        legacy._canonical(_candidate_compact(candidate)).encode("utf-8"))


def _validate_source_binding(source_output_dir, legacy_manifest_hash):
    binding_path = os.path.join(source_output_dir, "manifest-binding.json")
    binding = _load_json(binding_path, "source replay manifest binding")
    if binding.get("schema") != legacy.BINDING_SCHEMA or \
            binding.get("manifest_sha256") != legacy_manifest_hash:
        raise CandidateReplayError(
            "source replay output is bound to a different legacy manifest")
    return {
        "path": os.path.realpath(binding_path),
        "sha256": legacy._sha_file(binding_path),
    }


def _cache_data(path, expected_hash=None):
    if expected_hash is not None and legacy._sha_file(path) != expected_hash:
        raise CandidateReplayError("source miner cache hash changed")
    data = _load_json(path, "source miner cache")
    if data.get("schema") != legacy.MINER_CACHE_SCHEMA:
        raise CandidateReplayError("source miner cache schema mismatch")
    if data.get("miner_policy") != legacy._miner.MINER_POLICY_VERSION:
        raise CandidateReplayError("source miner cache policy mismatch")
    if not isinstance(data.get("candidates"), list) or \
            not isinstance(data.get("meta"), dict):
        raise CandidateReplayError("source miner cache contract mismatch")
    if data["meta"].get("error"):
        raise CandidateReplayError("source miner cache contains a miner error")
    if data["meta"].get("miner_policy") not in (
            None, legacy._miner.MINER_POLICY_VERSION):
        raise CandidateReplayError("source miner cache meta policy mismatch")
    return data


def build_inventory(legacy_manifest_path, source_output_dir):
    """Build a claim/quote-free inventory of unique current-miner candidates."""
    legacy_manifest = _load_source_legacy_manifest(legacy_manifest_path)
    legacy_manifest_hash = legacy._manifest_hash(legacy_manifest)
    source_output_dir = os.path.realpath(source_output_dir)
    if os.path.islink(source_output_dir) or not os.path.isdir(source_output_dir):
        raise CandidateReplayError("source replay output must be a real directory")
    binding_meta = _validate_source_binding(source_output_dir, legacy_manifest_hash)
    cache_dir = os.path.join(source_output_dir, "miner-cache")
    if os.path.islink(cache_dir) or not os.path.isdir(cache_dir):
        raise CandidateReplayError("source miner cache directory is missing or linked")

    transcripts = {}
    for row in legacy_manifest["rows"]:
        transcript = row.get("transcript") or {}
        if transcript.get("status") != "resolved" or not transcript.get("sha256"):
            continue
        key = transcript["sha256"]
        prior = transcripts.get(key)
        if prior and (prior.get("path") != transcript.get("path") or
                      prior.get("bytes") != transcript.get("bytes")):
            raise CandidateReplayError("legacy manifest has conflicting transcript metadata")
        transcripts[key] = dict(transcript)

    rows_by_id = {}
    source_cache_files = []
    cached_kind_counts = Counter()
    total_cached = 0
    duplicate_occurrences = 0
    cache_paths = sorted(
        os.path.join(cache_dir, name) for name in os.listdir(cache_dir)
        if name.endswith(".json"))
    for path in cache_paths:
        if not legacy._inside(path, cache_dir) or os.path.islink(path):
            raise CandidateReplayError("source miner cache path escapes or is linked")
        cache_hash = legacy._sha_file(path)
        data = _cache_data(path, cache_hash)
        transcript_sha = str(data.get("transcript_sha256") or "")
        transcript = transcripts.get(transcript_sha)
        if not transcript:
            raise CandidateReplayError("source miner cache transcript is not in manifest")
        source_cache_files.append({
            "path": os.path.realpath(path),
            "sha256": cache_hash,
            "transcript_sha256": transcript_sha,
            "candidate_count": len(data["candidates"]),
        })
        total_cached += len(data["candidates"])
        for index, candidate in enumerate(data["candidates"]):
            if not isinstance(candidate, dict):
                raise CandidateReplayError("source miner cache candidate is not an object")
            kind = str(candidate.get("kind") or "")
            cached_kind_counts[kind or "<missing>"] += 1
            if kind not in legacy._miner.ACQ_KINDS:
                continue
            compact = _candidate_compact(candidate)
            if not compact["claim"] or not compact["transcript_quote"]:
                raise CandidateReplayError("acquisition candidate lacks claim or quote")
            if compact["miner_policy"] != data["miner_policy"]:
                raise CandidateReplayError(
                    "acquisition candidate miner policy differs from its cache")
            if legacy._contains_secret(legacy._canonical(compact)):
                raise CandidateReplayError(
                    "secret-like material found in source miner cache; inventory aborted")
            candidate_id = _candidate_id(transcript_sha, candidate)
            occurrence = {
                "cache_path": os.path.realpath(path),
                "cache_sha256": cache_hash,
                "candidate_index": index,
            }
            if candidate_id in rows_by_id:
                rows_by_id[candidate_id]["source_occurrences"].append(occurrence)
                duplicate_occurrences += 1
                continue
            rows_by_id[candidate_id] = {
                "candidate_id": candidate_id,
                "candidate_body_sha256": _candidate_body_sha(candidate),
                "kind": kind,
                "miner_policy": data["miner_policy"],
                "transcript": transcript,
                "source_occurrences": [occurrence],
            }

    prior_results_path = os.path.join(source_output_dir, "replay-results.jsonl")
    prior_results = None
    if os.path.isfile(prior_results_path) and not os.path.islink(prior_results_path):
        prior_rows, malformed = legacy._read_jsonl(prior_results_path)
        if malformed:
            raise CandidateReplayError("source replay results contain malformed lines")
        prior_results = {
            "path": os.path.realpath(prior_results_path),
            "sha256": legacy._sha_file(prior_results_path),
            "record_count": len(prior_rows),
        }

    rows = list(rows_by_id.values())
    rows.sort(key=lambda row: (
        row["transcript"]["sha256"],
        row["source_occurrences"][0]["candidate_index"],
        row["candidate_id"],
    ))
    return {
        "schema": INVENTORY_SCHEMA,
        "created_at": legacy._now_iso(),
        "repo_head": legacy._git_head(os.path.dirname(_BIN)),
        "policy_identity": _policy_identity(),
        "memory_system": legacy_manifest["memory_system"],
        "projects_root": legacy_manifest["projects_root"],
        "source_legacy_manifest": {
            "path": os.path.realpath(legacy_manifest_path),
            "sha256": legacy._sha_file(legacy_manifest_path),
            "canonical_sha256": legacy_manifest_hash,
        },
        "source_replay_output": source_output_dir,
        "source_replay_binding": binding_meta,
        "source_prior_results": prior_results,
        "source_cache_files": source_cache_files,
        "rows": rows,
        "totals": {
            "source_cache_files": len(source_cache_files),
            "source_manifest_transcripts": len(transcripts),
            "source_transcripts_with_cache": len({
                item["transcript_sha256"] for item in source_cache_files}),
            "cached_candidates": total_cached,
            "cached_kind_distribution": dict(sorted(cached_kind_counts.items())),
            "unique_acquisition_candidates": len(rows),
            "acquisition_kind_distribution": dict(sorted(Counter(
                row["kind"] for row in rows).items())),
            "duplicate_candidate_occurrences": duplicate_occurrences,
        },
    }


def render_inventory_report(manifest):
    totals = manifest["totals"]
    return "\n".join([
        "# M3 legacy candidate-first inventory", "",
        "Aggregate-only manifest report. Claims, quotes, and transcript bodies are omitted.", "",
        "- Created: `%s`" % manifest["created_at"],
        "- Policy: `%s`" % manifest["policy_identity"]["candidate_replay_policy"],
        "- Current miner policy: `%s`" % manifest["policy_identity"]["miner_policy"],
        "- Source cache files: **%d**" % totals["source_cache_files"],
        "- Cached candidates: **%d**" % totals["cached_candidates"],
        "- Unique acquisition candidates: **%d**" %
        totals["unique_acquisition_candidates"],
        "- Duplicate candidate occurrences collapsed: **%d**" %
        totals["duplicate_candidate_occurrences"],
        "- Kind distribution: `%s`" % legacy._canonical(
            totals["acquisition_kind_distribution"]), "",
        "## Safety", "",
        "- Source replay artifacts are read-only and hash-bound.",
        "- The inventory contains no claim, quote, or transcript body fields.",
        "- Candidate replay output is excluded from D5 and has no import mode.", "",
    ])


def _load_inventory(path, require_current_policy=True):
    manifest = _load_json(path, "candidate inventory")
    if manifest.get("schema") != INVENTORY_SCHEMA:
        raise CandidateReplayError("invalid candidate inventory schema")
    if require_current_policy and manifest.get("policy_identity") != _policy_identity():
        raise CandidateReplayError("candidate replay code or policy differs from inventory")
    rows = manifest.get("rows")
    if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not row.get("candidate_id")
            for row in rows):
        raise CandidateReplayError("candidate inventory rows are invalid")
    if len({row["candidate_id"] for row in rows}) != len(rows):
        raise CandidateReplayError("candidate inventory IDs are not unique")
    source = manifest.get("source_legacy_manifest") or {}
    source_path = source.get("path") or ""
    if not source_path or not os.path.isfile(source_path) or \
            os.path.islink(source_path) or \
            legacy._sha_file(source_path) != source.get("sha256"):
        raise CandidateReplayError("source legacy manifest file changed")
    legacy_manifest = _load_source_legacy_manifest(source_path)
    if legacy._manifest_hash(legacy_manifest) != source.get("canonical_sha256"):
        raise CandidateReplayError("source legacy manifest canonical hash changed")
    _validate_source_binding(
        manifest["source_replay_output"], source["canonical_sha256"])
    return manifest


def _load_candidate(meta):
    loaded = None
    for occurrence in meta.get("source_occurrences") or []:
        path = occurrence.get("cache_path") or ""
        data = _cache_data(path, occurrence.get("cache_sha256"))
        if data.get("transcript_sha256") != meta["transcript"]["sha256"]:
            raise CandidateReplayError("candidate cache transcript binding changed")
        index = occurrence.get("candidate_index")
        if not isinstance(index, int) or index < 0 or index >= len(data["candidates"]):
            raise CandidateReplayError("candidate cache index is invalid")
        candidate = data["candidates"][index]
        if not isinstance(candidate, dict) or \
                _candidate_id(meta["transcript"]["sha256"], candidate) != \
                meta["candidate_id"] or \
                _candidate_body_sha(candidate) != meta["candidate_body_sha256"]:
            raise CandidateReplayError("candidate body differs from inventory")
        if loaded is None:
            loaded = candidate
    if loaded is None:
        raise CandidateReplayError("candidate has no source occurrence")
    return _candidate_compact(loaded)


def _load_prior_entailments(manifest):
    prior = manifest.get("source_prior_results")
    if not prior:
        return {}
    path = prior.get("path") or ""
    if not path or not os.path.isfile(path) or os.path.islink(path) or \
            legacy._sha_file(path) != prior.get("sha256"):
        raise CandidateReplayError("source replay results hash changed")
    rows, malformed = legacy._read_jsonl(path)
    if malformed:
        raise CandidateReplayError("source replay results contain malformed lines")
    source_hash = manifest["source_legacy_manifest"]["canonical_sha256"]
    source_manifest = _load_source_legacy_manifest(
        manifest["source_legacy_manifest"]["path"])
    source_rows = {row["replay_row_id"]: row for row in source_manifest["rows"]}
    source_miner_policy = source_manifest["policy_identity"]["miner_policy"]
    latest = {}
    for row in rows:
        if row.get("record_type") in ("result", "recovery_marker") and \
                row.get("manifest_sha256") != source_hash:
            raise CandidateReplayError("source replay result manifest binding changed")
        if row.get("record_type") == "result" and row.get("replay_row_id"):
            latest[row["replay_row_id"]] = row
    entailed = {}
    for row in latest.values():
        if row.get("final_outcome") != "salvaged_current_policy":
            continue
        source_row = source_rows.get(row.get("replay_row_id"))
        judge = row.get("judge") or {}
        rail = row.get("deterministic_rail") or {}
        if row.get("schema") != legacy.RESULT_SCHEMA or \
                row.get("origin") != "legacy_replay" or \
                row.get("replay_policy") != legacy.REPLAY_POLICY or \
                row.get("current_miner_policy") != source_miner_policy or \
                not source_row or \
                row.get("transcript_sha256") != \
                (source_row.get("transcript") or {}).get("sha256") or \
                rail.get("outcome") != "survived" or \
                not judge.get("invoked") or \
                judge.get("outcome") != "entailed" or \
                not judge.get("quote_verified"):
            raise CandidateReplayError(
                "source salvaged result fails the entailment reuse contract")
        candidate = {
            "kind": row.get("kind"),
            "claim": row.get("claim"),
            "transcript_quote": row.get("supporting_quote"),
            "miner_policy": row.get("current_miner_policy"),
        }
        if candidate["kind"] not in legacy._miner.ACQ_KINDS or \
                not candidate["claim"] or not candidate["transcript_quote"] or \
                legacy._contains_secret(legacy._canonical(candidate)):
            raise CandidateReplayError(
                "source salvaged result has invalid candidate evidence")
        cid = _candidate_id(row.get("transcript_sha256"), candidate)
        entailed[cid] = {
            "source_results_path": os.path.realpath(path),
            "source_results_sha256": prior["sha256"],
            "source_replay_row_id": row["replay_row_id"],
            "route": (row.get("judge") or {}).get("route"),
            "model": (row.get("judge") or {}).get("model"),
            "outcome": "entailed",
        }
    return entailed


def _base_record(meta, manifest_hash):
    occurrence = meta["source_occurrences"][0]
    return {
        "schema": RESULT_SCHEMA,
        "record_type": "result",
        "origin": "legacy_candidate_replay",
        "candidate_replay_policy": POLICY,
        "manifest_sha256": manifest_hash,
        "candidate_id": meta["candidate_id"],
        "candidate_body_sha256": meta["candidate_body_sha256"],
        "kind": meta["kind"],
        "miner_policy": meta["miner_policy"],
        "source_cache_path": occurrence["cache_path"],
        "source_cache_sha256": occurrence["cache_sha256"],
        "source_candidate_index": occurrence["candidate_index"],
        "transcript_path": meta["transcript"].get("path"),
        "transcript_sha256": meta["transcript"].get("sha256"),
        "replay_timestamp": legacy._now_iso(),
        "deterministic_rail": {"outcome": None, "reason": None},
        "quote_verification": {"exact": False, "assistant_turn_indices": []},
        "later_context": {
            "later_assistant_turn_count": None,
            "possible_correction_hits": [],
            "manual_supersession_review_required": True,
        },
        "relation": {
            "type": None,
            "related_id": None,
            "mode": "lexical_v1",
            "semantic_review_required": True,
        },
        "judge": {
            "invoked": False,
            "reused": False,
            "route": None,
            "model": None,
            "outcome": None,
            "quote_verified": False,
        },
        "error": None,
    }


def _finish(record, outcome, reason, error=None):
    if outcome not in ALL_OUTCOMES:
        raise CandidateReplayError("invalid candidate replay outcome")
    record["final_outcome"] = outcome
    record["final_reason"] = reason
    record["error"] = error
    return record


def _full_turns(path):
    size = os.path.getsize(path)
    return legacy._miner.read_turns(
        path, tail_bytes=max(size + 1, legacy._miner.TAIL_BYTES),
        max_turns=10 ** 9)


def _later_context(claim, quote, turns):
    indices = [index for index, (role, text) in enumerate(turns)
               if role == "assistant" and quote in text]
    if not indices:
        return indices, {
            "later_assistant_turn_count": None,
            "possible_correction_hits": [],
            "manual_supersession_review_required": True,
        }
    # Scan from the earliest evidence occurrence. A later correction can quote
    # the original sentence verbatim; anchoring at the latest occurrence would
    # incorrectly put that correction turn outside the scan window.
    first = min(indices)
    later = [(index, text) for index, (role, text) in enumerate(turns)
             if index > first and role == "assistant"]
    hits = []
    for index, text in later:
        score = legacy._similarity(claim, text, ignore_negation=True)
        opposite = legacy._polarity(claim) != legacy._polarity(text)
        correction_term = bool(_CORRECTION_RE.search(text))
        if (opposite and score >= legacy.CONFLICT_FLOOR) or \
                (correction_term and score >= legacy.SIMILARITY_FLOOR):
            hits.append({
                "assistant_turn_index": index,
                "text_sha256": legacy._sha_bytes(text.encode("utf-8")),
                "similarity": round(score, 6),
                "opposite_polarity": bool(opposite),
                "correction_term": bool(correction_term),
            })
    return indices, {
        "later_assistant_turn_count": len(later),
        "possible_correction_hits": hits,
        "manual_supersession_review_required": True,
    }


def _evaluate(meta, candidate, manifest_hash, enable_judge,
              prior_entailments, accepted, production):
    record = _base_record(meta, manifest_hash)
    compact = _candidate_compact(candidate)
    claim = compact["claim"].strip()
    quote = compact["transcript_quote"].strip()
    if legacy._contains_secret(legacy._canonical(compact)):
        return _finish(record, "invalid_evidence", "secret_bearing_candidate")
    record.update({"claim": claim, "supporting_quote": quote})
    reject = legacy._miner.durability_reject_reason(compact)
    if reject:
        record["deterministic_rail"] = {"outcome": "rejected", "reason": reject}
        return _finish(record, "rejected_noise", reject)
    record["deterministic_rail"] = {"outcome": "survived", "reason": None}

    transcript = meta.get("transcript") or {}
    path = transcript.get("path")
    if transcript.get("status") != "resolved" or not path or not os.path.isfile(path):
        return _finish(record, "invalid_evidence", "transcript_missing")
    if legacy._sha_file(path) != transcript.get("sha256"):
        return _finish(record, "invalid_evidence", "transcript_hash_changed")
    try:
        turns = _full_turns(path)
    except Exception as exc:
        return _finish(record, "invalid_evidence", "transcript_parse_failed",
                       {"class": type(exc).__name__})
    if legacy._sha_file(path) != transcript.get("sha256"):
        return _finish(record, "invalid_evidence",
                       "transcript_changed_during_quote_verification")
    indices, later = _later_context(claim, quote, turns)
    record["quote_verification"] = {
        "exact": bool(indices), "assistant_turn_indices": indices,
    }
    record["judge"]["quote_verified"] = bool(indices)
    record["later_context"] = later
    if not indices:
        return _finish(record, "invalid_evidence", "quote_not_in_transcript")
    if later["possible_correction_hits"]:
        return _finish(record, "conflict_manual_review",
                       "possible_later_correction")

    relation, related = legacy._relation(claim, accepted, production)
    record["relation"].update({"type": relation, "related_id": related})
    if relation:
        return _finish(record, "conflict_manual_review", relation)

    prior = prior_entailments.get(meta["candidate_id"])
    if prior:
        record["judge"].update({
            "invoked": False,
            "reused": True,
            "route": prior.get("route"),
            "model": prior.get("model"),
            "outcome": "entailed",
            "prior_evidence": prior,
        })
        return _finish(record, "review_candidate", "reused_bound_entailment")
    if not enable_judge:
        return _finish(record, "transient_retry", "judge_not_enabled")

    record["judge"].update({
        "invoked": True,
        "route": str(getattr(legacy._judge, "_JUDGE_PROVIDER", "")),
        "model": str(getattr(legacy._judge, "_JUDGE_MODEL", "")),
    })
    try:
        verdict = legacy._side_effect_free_judge_verdict(claim, [quote])
    except Exception as exc:
        verdict = "judge_unavailable"
        record["error"] = {"class": type(exc).__name__}
    record["judge"]["outcome"] = verdict
    if verdict == "entailed":
        return _finish(record, "review_candidate", "entailed_quote_verified")
    if verdict == "not_entailed":
        return _finish(record, "invalid_evidence", "judge_not_entailed")
    return _finish(record, "transient_retry", "judge_unavailable_or_malformed")


def _bind_output(output_dir, manifest_hash):
    path = os.path.join(output_dir, "candidate-manifest-binding.json")
    artifacts = (
        os.path.join(output_dir, "candidate-results.jsonl"),
        os.path.join(output_dir, "candidate-checkpoint.json"),
        os.path.join(output_dir, "candidate-summary.json"),
    )
    if not os.path.lexists(path):
        if any(os.path.lexists(item) for item in artifacts):
            raise CandidateReplayError(
                "existing candidate replay output lacks immutable binding")
        legacy._atomic_json(path, {
            "schema": BINDING_SCHEMA,
            "created_at": legacy._now_iso(),
            "manifest_sha256": manifest_hash,
        })
    else:
        binding = _load_json(path, "candidate manifest binding")
        if binding.get("schema") != BINDING_SCHEMA or \
                binding.get("manifest_sha256") != manifest_hash:
            raise CandidateReplayError(
                "candidate replay output is bound to a different manifest")
    checkpoint_path = artifacts[1]
    if os.path.lexists(checkpoint_path):
        checkpoint = _load_json(checkpoint_path, "candidate checkpoint")
        if checkpoint.get("schema") != CHECKPOINT_SCHEMA or \
                checkpoint.get("manifest_sha256") != manifest_hash:
            raise CandidateReplayError("candidate checkpoint binding changed")


def _prepare_append(path, manifest_hash):
    legacy._reject_symlink(path)
    if not os.path.lexists(path) or os.path.getsize(path) == 0:
        return False
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as fh:
        fh.seek(-1, os.SEEK_END)
        complete = fh.read(1) == b"\n"
    if complete:
        return False
    legacy._append_bytes(path, "\n")
    legacy._append_record(path, {
        "schema": RESULT_SCHEMA,
        "record_type": "recovery_marker",
        "manifest_sha256": manifest_hash,
        "replay_timestamp": legacy._now_iso(),
        "reason": "truncated_final_append_preserved",
    })
    return True


def _latest_results(path, manifest_hash):
    legacy._reject_symlink(path)
    rows, malformed = legacy._read_jsonl(path)
    latest, terminals = {}, {}
    for row in rows:
        if row.get("record_type") in ("result", "recovery_marker") and \
                row.get("manifest_sha256") != manifest_hash:
            raise CandidateReplayError("candidate result manifest binding changed")
        if row.get("record_type") != "result" or not row.get("candidate_id"):
            continue
        candidate_id = row["candidate_id"]
        latest[candidate_id] = row
        if row.get("final_outcome") in TERMINAL_OUTCOMES:
            terminals[candidate_id] = row
    return latest, terminals, malformed


def _verify_review_candidate(meta, result):
    if result.get("candidate_body_sha256") != meta.get("candidate_body_sha256") or \
            result.get("transcript_sha256") != meta["transcript"].get("sha256"):
        raise CandidateReplayError("terminal review candidate binding differs")
    candidate = _load_candidate(meta)
    if result.get("claim") != candidate["claim"] or \
            result.get("supporting_quote") != candidate["transcript_quote"]:
        raise CandidateReplayError("terminal review candidate body differs")
    path = meta["transcript"].get("path")
    if not os.path.isfile(path) or legacy._sha_file(path) != \
            meta["transcript"].get("sha256"):
        raise CandidateReplayError("terminal review transcript changed")
    turns = _full_turns(path)
    if not any(role == "assistant" and candidate["transcript_quote"] in text
               for role, text in turns):
        raise CandidateReplayError("terminal review quote no longer verifies")
    if legacy._contains_secret(legacy._canonical(candidate)):
        raise CandidateReplayError("terminal review candidate contains a secret")


def _summary(selected, results_path, manifest_hash):
    latest, terminals, malformed = _latest_results(results_path, manifest_hash)
    ids = [row["candidate_id"] for row in selected]
    terminal = {cid: terminals[cid] for cid in ids if cid in terminals}
    retries = {cid: latest[cid] for cid in ids if cid not in terminal and
               latest.get(cid, {}).get("final_outcome") == "transient_retry"}
    pending = [cid for cid in ids if cid not in terminal and cid not in retries]
    outcomes = Counter(row["final_outcome"] for row in terminal.values())
    reasons = Counter(row.get("final_reason") or "<missing>"
                      for row in list(terminal.values()) + list(retries.values()))
    return {
        "schema": SUMMARY_SCHEMA,
        "generated_at": legacy._now_iso(),
        "selected_input_count": len(ids),
        "terminal_count": len(terminal),
        "nonterminal_retry_count": len(retries),
        "pending_count": len(pending),
        "terminal_outcomes": dict(sorted(outcomes.items())),
        "reason_distribution": dict(sorted(reasons.items())),
        "malformed_result_lines_preserved": len(malformed),
        "reconciled": len(ids) == len(terminal) + len(retries) + len(pending),
    }


def run_replay(manifest, output_dir, offset=0, limit=None, workers=1,
               enable_judge=False, production_roots=()):
    workers = int(workers)
    if workers != MAX_WORKERS:
        raise CandidateReplayError(
            "only workers=1 is admitted for sequential Writer judge execution")
    output_dir = os.path.realpath(output_dir)
    source_dir = manifest["source_replay_output"]
    roots = legacy._canonical_production_roots(
        manifest["projects_root"], production_roots)
    legacy._guard_output(output_dir, manifest["memory_system"],
                         manifest["projects_root"], roots)
    legacy._guard_distinct_output(output_dir, protected_dirs=(source_dir,))
    os.makedirs(output_dir, mode=0o700, exist_ok=True)
    with legacy._output_lock(output_dir):
        manifest_hash = legacy._manifest_hash(manifest)
        _bind_output(output_dir, manifest_hash)
        results_path = os.path.join(output_dir, "candidate-results.jsonl")
        checkpoint_path = os.path.join(output_dir, "candidate-checkpoint.json")
        summary_path = os.path.join(output_dir, "candidate-summary.json")
        for path in (results_path, checkpoint_path, summary_path):
            legacy._reject_symlink(path)
        _prepare_append(results_path, manifest_hash)

        selected = manifest["rows"][max(0, offset):]
        if limit is not None:
            selected = selected[:max(0, limit)]
        # Validate every selected cache and transcript before the first judge call.
        loaded = []
        for meta in selected:
            candidate = _load_candidate(meta)
            transcript = meta.get("transcript") or {}
            path = transcript.get("path")
            if transcript.get("status") != "resolved" or not path or \
                    not os.path.isfile(path) or \
                    legacy._sha_file(path) != transcript.get("sha256"):
                raise CandidateReplayError(
                    "selected candidate transcript differs from inventory")
            loaded.append((meta, candidate))

        latest, terminals, _malformed = _latest_results(results_path, manifest_hash)
        selected_by_id = {meta["candidate_id"]: meta for meta, _ in loaded}
        for cid, result in terminals.items():
            if cid in selected_by_id and result.get("final_outcome") == \
                    "review_candidate":
                _verify_review_candidate(selected_by_id[cid], result)
        prior_entailments = _load_prior_entailments(manifest)
        production = legacy._production_documents(roots)
        accepted = []
        for row in terminals.values():
            if row.get("final_outcome") != "review_candidate":
                continue
            relation_row = dict(row)
            relation_row["replay_row_id"] = row.get("candidate_id")
            accepted.append(relation_row)
        pending = [(meta, candidate) for meta, candidate in loaded
                   if meta["candidate_id"] not in terminals]
        for meta, candidate in pending:
            record = _evaluate(
                meta, candidate, manifest_hash, enable_judge,
                prior_entailments, accepted, production)
            if record.get("final_outcome") == "review_candidate":
                relation_row = dict(record)
                relation_row["replay_row_id"] = record.get("candidate_id")
                accepted.append(relation_row)
            # Persist each completed unit before the next external judge call.
            # A process stop therefore replays only unfinished candidates.
            legacy._append_record(results_path, record)

        summary = _summary(selected, results_path, manifest_hash)
        latest, terminals, malformed = _latest_results(results_path, manifest_hash)
        ids = {row["candidate_id"] for row in selected}
        checkpoint = {
            "schema": CHECKPOINT_SCHEMA,
            "updated_at": legacy._now_iso(),
            "manifest_sha256": manifest_hash,
            "selected_offset": offset,
            "selected_limit": limit,
            "judge_enabled": bool(enable_judge),
            "workers": workers,
            "completed_candidate_ids": sorted(cid for cid in terminals if cid in ids),
            "retry_candidate_ids": sorted(
                cid for cid, row in latest.items() if cid in ids and
                cid not in terminals and row.get("final_outcome") ==
                "transient_retry"),
            "malformed_result_lines_preserved": len(malformed),
        }
        legacy._atomic_json(summary_path, summary)
        legacy._atomic_json(checkpoint_path, checkpoint)
        return summary, results_path


def render_review_packet(manifest, results_path, sample_size=8):
    manifest_hash = legacy._manifest_hash(manifest)
    latest, terminals, malformed = _latest_results(results_path, manifest_hash)
    ids = [row["candidate_id"] for row in manifest["rows"]]
    terminal_rows = [terminals[cid] for cid in ids if cid in terminals]
    retry_rows = [latest[cid] for cid in ids if cid not in terminals and
                  latest.get(cid, {}).get("final_outcome") == "transient_retry"]
    pending = len(ids) - len(terminal_rows) - len(retry_rows)
    candidates = [row for row in terminal_rows
                  if row.get("final_outcome") == "review_candidate"]
    by_id = {row["candidate_id"]: row for row in manifest["rows"]}
    for row in candidates:
        _verify_review_candidate(by_id[row["candidate_id"]], row)
    outcomes = Counter(row["final_outcome"] for row in terminal_rows)
    reasons = Counter(row.get("final_reason") or "<missing>"
                      for row in terminal_rows + retry_rows)
    lines = [
        "# M3 legacy candidate-first human review packet", "",
        "Replay-only artifact. Nothing here is imported or counted toward D5.", "",
        "## Accounting", "",
        "- Manifest candidates: **%d**" % len(ids),
        "- Terminal: **%d**" % len(terminal_rows),
        "- Non-terminal retries: **%d**" % len(retry_rows),
        "- Pending: **%d**" % pending,
        "- Reconciled: **%s**" % str(
            len(ids) == len(terminal_rows) + len(retry_rows) + pending).lower(),
        "- Malformed result lines preserved: **%d**" % len(malformed),
        "- Outcomes: `%s`" % legacy._canonical(dict(sorted(outcomes.items()))),
        "- Reasons: `%s`" % legacy._canonical(dict(sorted(reasons.items()))),
        "", "## Review candidates", "",
    ]
    if not candidates:
        lines.append("No candidate has passed the entailment gate yet.")
    for index, row in enumerate(candidates, 1):
        later = row.get("later_context") or {}
        lines += [
            "### %d. `%s` [%s]" % (
                index, row["candidate_id"][:12], row.get("kind")), "",
            "Claim: %s" % legacy._review_claim(row.get("claim")), "",
            "Exact supporting quote (JSON string): `%s`" %
            legacy._canonical(row.get("supporting_quote") or ""), "",
            "Source: `%s#candidate-%s` · transcript sha256 `%s`" % (
                os.path.basename(row.get("source_cache_path") or "cache"),
                row.get("source_candidate_index"), row.get("transcript_sha256")), "",
            "Judge: route `%s/%s` · outcome `%s` · reused `%s`" % (
                (row.get("judge") or {}).get("route") or "unknown",
                (row.get("judge") or {}).get("model") or "unknown",
                (row.get("judge") or {}).get("outcome") or "unknown",
                str(bool((row.get("judge") or {}).get("reused"))).lower()), "",
            "Later assistant turns after the earliest quote occurrence: **%s**; "
            "deterministic correction hits: **%d**." % (
                later.get("later_assistant_turn_count"),
                len(later.get("possible_correction_hits") or [])), "",
            "Review: [ ] still durable  [ ] later corrections checked  "
            "[ ] semantically novel vs production  [ ] accept  [ ] reject", "",
        ]
    lines += ["", "## Rejections and conflicts", ""]
    grouped = defaultdict(list)
    for row in terminal_rows:
        if row.get("final_outcome") != "review_candidate":
            grouped[row.get("final_reason") or "<missing>"].append(row)
    if not grouped:
        lines.append("- None.")
    for reason in sorted(grouped):
        lines.append("- `%s`: **%d**" % (reason, len(grouped[reason])))
        for row in grouped[reason][:sample_size]:
            lines.append("  - `%s` — `%s#candidate-%s`" % (
                row["candidate_id"][:12],
                os.path.basename(row.get("source_cache_path") or "cache"),
                row.get("source_candidate_index")))
    lines += ["", "## Stop condition", "",
              "No automatic import action exists. Human review is mandatory, and "
              "legacy candidate replay remains excluded from D5.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory")
    inv.add_argument("--legacy-manifest", required=True)
    inv.add_argument("--source-output-dir", required=True)
    inv.add_argument("--manifest", required=True)
    inv.add_argument("--report", required=True)

    rep = sub.add_parser("replay")
    rep.add_argument("--manifest", required=True)
    rep.add_argument("--output-dir", required=True)
    rep.add_argument("--offset", type=int, default=0)
    rep.add_argument("--limit", type=int, default=None)
    rep.add_argument("--workers", type=int, default=1)
    rep.add_argument("--enable-judge", action="store_true")
    rep.add_argument("--production-root", action="append", default=[])

    rev = sub.add_parser("review")
    rev.add_argument("--manifest", required=True)
    rev.add_argument("--results", required=True)
    rev.add_argument("--output", required=True)
    rev.add_argument("--sample-size", type=int, default=8)
    args = parser.parse_args(argv)

    if args.command == "inventory":
        legacy_manifest = _load_source_legacy_manifest(args.legacy_manifest)
        roots = legacy._canonical_production_roots(legacy_manifest["projects_root"])
        for output in (args.manifest, args.report):
            legacy._guard_output(output, legacy_manifest["memory_system"],
                                 legacy_manifest["projects_root"], roots)
            legacy._guard_distinct_output(
                output, (args.legacy_manifest,), (args.source_output_dir,))
        legacy._guard_distinct_output(args.manifest, (args.report,))
        manifest = build_inventory(args.legacy_manifest, args.source_output_dir)
        legacy._atomic_json(args.manifest, manifest)
        legacy._write_text(args.report, render_inventory_report(manifest))
        print(legacy._canonical({"status": "ok", "replay_only": True,
                                 "production_write": False,
                                 "manifest": args.manifest,
                                 "totals": manifest["totals"]}))
        return 0
    if args.command == "replay":
        manifest = _load_inventory(args.manifest)
        summary, results = run_replay(
            manifest, args.output_dir, offset=args.offset, limit=args.limit,
            workers=args.workers, enable_judge=args.enable_judge,
            production_roots=args.production_root)
        print(legacy._canonical({
            "status": "ok", "replay_only": True,
            "production_write": False,
            "judge_enabled": args.enable_judge,
            "results": results, "summary": summary,
        }))
        return 0

    manifest = _load_inventory(args.manifest, require_current_policy=False)
    roots = legacy._canonical_production_roots(manifest["projects_root"])
    legacy._guard_output(args.output, manifest["memory_system"],
                         manifest["projects_root"], roots)
    result_dir = os.path.dirname(os.path.realpath(args.results))
    legacy._guard_distinct_output(
        args.output,
        (args.manifest, args.results,
         os.path.join(result_dir, "candidate-manifest-binding.json"),
         os.path.join(result_dir, "candidate-checkpoint.json"),
         os.path.join(result_dir, "candidate-summary.json")),
        (manifest["source_replay_output"],))
    packet = render_review_packet(manifest, args.results, args.sample_size)
    legacy._write_text(args.output, packet)
    print(legacy._canonical({"status": "ok", "replay_only": True,
                             "production_write": False,
                             "output": args.output}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
