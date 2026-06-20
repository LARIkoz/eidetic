#!/usr/bin/env python3
"""Eidetic — aligned vector-coverage audit (P0).

Read-only. Classifies every collect_files() entry against the REAL search-time
guard (search_impl.py:825-841): a vector counts only if it joins to a live
memory_chunks row BY chunk_id AND its path+heading+content_hash match. This is
the truth the gross doctor formula ((chunks-vectors)*100/chunks) hides: a file
can have rows in index.db yet zero usable vectors.

Mirrors the guard exactly — joins by chunk_id first (chunk_ids churn on every
re-index: index_file does DELETE WHERE path + re-INSERT, so an out-of-sync
vectors.db yields orphan-vectors, not silent staleness), then recomputes
content_hash via embed.content_hash (the sole hash authority; never re-derived
here — it owns the content[:500] slice + HASH_SCHEME).

Categories (per chunk, the current truth):
  aligned             vector exists, path+heading+hash all match (guard accepts)
  indexed-no-vector   chunk row exists, no vector at its chunk_id
  stale-hash          vector at chunk_id, path+heading match, hash mismatch
                      (only a HASH_SCHEME bump can cause this — an edit churns
                       the chunk_id, producing no-vector + orphan, not stale-hash)
  stale-vector        vector at chunk_id but path/heading mismatch (id reused by
                      a different chunk → guard rejects → current chunk is blind)
Vector-side:
  orphan-vector       vector whose chunk_id is in NO live memory_chunks row
Zero-row files (in collect_files, 0 rows in memory_chunks) get a closed-vocab
reason: empty-body / non-utf8 / parse-error / absent-from-index (the last = a
bug: real body but never indexed).

Zero-dep: stdlib + sqlite3 (no fastembed; content_hash is model-free).
Usage:  coverage_audit.py [index.db] [vectors.db] [--json]
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import embed          # noqa: E402  (import-safe: fastembed/numpy are lazy)
import index_impl     # noqa: E402


def _default_dbs():
    base = os.path.expanduser(
        os.environ.get("EIDETIC_MEMORY_SYSTEM", "~/.claude/memory-system")
    )
    return os.path.join(base, "db", "index.db"), os.path.join(base, "db", "vectors.db")


def is_memory_file(path):
    """A core memory file (the 975-file headline scope): projects/*/memory/**.

    Includes the memory/signals/ subdir; excludes ~/.claude/skills/*/SKILL.md and
    bare ~/.claude/agent-memory/ which collect_files() also returns.
    """
    return "/.claude/projects/" in path and "/memory/" in path


def _zero_row_reason(path):
    """Closed-vocab reason for a collect_files entry with no memory_chunks rows."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return "parse-error"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "non-utf8"
    try:
        _meta, body = index_impl.parse_frontmatter(text)
    except Exception:
        return "parse-error"
    if not body.strip():
        return "empty-body"
    # Real body but no rows: index_file's `if body.strip()` gate should have
    # indexed it. Something dropped it — a bug, not a benign empty/parse case.
    return "absent-from-index"


def audit(index_db, vectors_db):
    ic = sqlite3.connect(f"file:{index_db}?mode=ro", uri=True)
    vc = sqlite3.connect(f"file:{vectors_db}?mode=ro", uri=True)
    try:
        # current truth: every chunk row
        chunks = {}            # id -> (path, name, description, content, section_heading)
        ids_by_path = {}       # path -> [chunk_id, ...]
        for cid, path, name, desc, content, heading in ic.execute(
            "SELECT id, path, name, description, content, section_heading FROM memory_chunks"
        ):
            chunks[cid] = (path, name, desc, content, heading)
            ids_by_path.setdefault(path, []).append(cid)

        # every vector row, keyed by chunk_id (PRIMARY KEY → 0/1 per id)
        vectors = {}           # chunk_id -> (path, name, section_heading, content_hash)
        for vid, vpath, vname, vheading, vhash in vc.execute(
            "SELECT chunk_id, path, name, section_heading, content_hash FROM vectors"
        ):
            vectors[vid] = (vpath, vname, vheading or "", vhash or "")
    finally:
        ic.close()
        vc.close()

    # Pass A — classify each live chunk (mirror the guard, in its order)
    chunk_cat = {}             # chunk_id -> category
    for cid, (cpath, cname, cdesc, ccontent, cheading) in chunks.items():
        vec = vectors.get(cid)
        if vec is None:
            chunk_cat[cid] = "indexed-no-vector"
            continue
        vpath, vname, vheading, vhash = vec
        if cpath != vpath or (cheading or "") != vheading:   # guard line 835
            chunk_cat[cid] = "stale-vector"
            continue
        if not vhash:                                        # guard line 837
            chunk_cat[cid] = "stale-hash"
            continue
        digest = embed.content_hash(vname, cdesc, ccontent, cheading)  # guard 839
        chunk_cat[cid] = "aligned" if digest == vhash else "stale-hash"

    # Pass B — orphan vectors (chunk_id absent from live rows)
    orphan_vectors = sum(1 for vid in vectors if vid not in chunks)

    # Pass C — files
    files = index_impl.collect_files(index_impl.memory_system_from_db(index_db))
    zero_row_reasons = {"empty-body": 0, "non-utf8": 0,
                        "parse-error": 0, "absent-from-index": 0}

    def blank_scope():
        return {"files_total": 0, "files_aligned": 0, "files_partial": 0,
                "files_blind": 0, "files_zero_row": 0,
                "chunks_total": 0, "chunks_aligned": 0, "chunks_blind": 0}

    scopes = {"all": blank_scope(), "memory": blank_scope()}
    categories = {"aligned": 0, "indexed-no-vector": 0,
                  "stale-hash": 0, "stale-vector": 0}

    seen_paths = set()
    for path in files:
        seen_paths.add(path)
        targets = [scopes["all"]] + ([scopes["memory"]] if is_memory_file(path) else [])
        for s in targets:
            s["files_total"] += 1

        cids = ids_by_path.get(path)
        if not cids:                                   # zero-row file
            reason = _zero_row_reason(path)
            zero_row_reasons[reason] += 1
            for s in targets:
                s["files_zero_row"] += 1
            continue

        n_aligned = 0
        for cid in cids:
            cat = chunk_cat[cid]
            categories[cat] += 1
            aligned = cat == "aligned"
            if aligned:
                n_aligned += 1
            for s in targets:
                s["chunks_total"] += 1
                if aligned:
                    s["chunks_aligned"] += 1
                else:
                    s["chunks_blind"] += 1

        for s in targets:
            if n_aligned == len(cids):
                s["files_aligned"] += 1
            elif n_aligned == 0:
                s["files_blind"] += 1
            else:
                s["files_partial"] += 1

    # index rows whose file is no longer collected (deleted/excluded) — informational
    index_orphan_files = sum(1 for p in ids_by_path if p not in seen_paths)

    def pct(num, den):
        return round(num * 100.0 / den, 1) if den else 0.0

    for s in scopes.values():
        s["aligned_file_pct"] = pct(s["files_aligned"], s["files_total"] - s["files_zero_row"])
        s["aligned_chunk_pct"] = pct(s["chunks_aligned"], s["chunks_total"])
        s["blind_chunk_pct"] = pct(s["chunks_blind"], s["chunks_total"])

    return {
        "db": {"index_db": index_db, "vectors_db": vectors_db},
        "scopes": scopes,
        "categories": categories,
        "orphan_vectors": orphan_vectors,
        "zero_row_reasons": zero_row_reasons,
        "index_orphan_files": index_orphan_files,
    }


def _print_human(r):
    m, a = r["scopes"]["memory"], r["scopes"]["all"]
    print("== Eidetic vector-coverage audit (aligned = guard would accept) ==")
    print(f"index.db   {r['db']['index_db']}")
    print(f"vectors.db {r['db']['vectors_db']}")
    print()
    print(f"MEMORY files (projects/*/memory/**): {m['files_total']}")
    print(f"  fully aligned : {m['files_aligned']}")
    print(f"  partially blind: {m['files_partial']}")
    print(f"  fully blind    : {m['files_blind']}  (>=1 chunk, none aligned)")
    print(f"  zero-row       : {m['files_zero_row']}")
    print(f"  aligned file % : {m['aligned_file_pct']}%   "
          f"(blind files: {m['files_partial'] + m['files_blind']})")
    print(f"  chunks {m['chunks_aligned']}/{m['chunks_total']} aligned "
          f"-> blind chunk % {m['blind_chunk_pct']}%")
    print()
    print("Chunk categories (ALL collect_files):")
    for k in ("aligned", "indexed-no-vector", "stale-hash", "stale-vector"):
        print(f"  {k:18} {r['categories'][k]}")
    print(f"  {'orphan-vector':18} {r['orphan_vectors']}  (vector chunk_id not in any live row)")
    print()
    zr = r["zero_row_reasons"]
    print("Zero-row file reasons:")
    for k in ("empty-body", "non-utf8", "parse-error", "absent-from-index"):
        flag = "  <-- BUG" if k == "absent-from-index" and zr[k] else ""
        print(f"  {k:18} {zr[k]}{flag}")
    print()
    print(f"ALL files: {a['files_total']} | aligned-chunk % {a['aligned_chunk_pct']}% | "
          f"index-orphan files (row, no file): {r['index_orphan_files']}")


def _oneline(r):
    """One line of shell-parseable KEY=VALUE facts — the truth doctor.sh /
    smart-memory-inject.sh consume INSTEAD of the gross (chunks-vectors)/chunks
    formula. Percentages are floored ints so bash can compare with -lt/-gt; the
    decimal lives in --json / human output. All values are bare numbers (no
    spaces) → `eval "$(coverage_audit.py --oneline)"` is safe."""
    a, m = r["scopes"]["all"], r["scopes"]["memory"]
    cats = r["categories"]
    stale = cats["stale-hash"] + cats["stale-vector"]
    return (
        f"aligned={a['chunks_aligned']} total={a['chunks_total']} "
        f"align_pct={int(a['aligned_chunk_pct'])} "
        f"orphan={r['orphan_vectors']} no_vector={cats['indexed-no-vector']} "
        f"stale={stale} blind_files={a['files_partial'] + a['files_blind']} "
        f"mem_pct={int(m['aligned_chunk_pct'])} "
        f"mem_blind={m['files_partial'] + m['files_blind']} "
        f"zero_row={m['files_zero_row']}"
    )


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in argv
    as_oneline = "--oneline" in argv
    argv = [a for a in argv if a not in ("--json", "--oneline")]
    di, dv = _default_dbs()
    if len(argv) >= 1:
        di = os.path.expanduser(argv[0])
    if len(argv) >= 2:
        dv = os.path.expanduser(argv[1])
    for p in (di, dv):
        if not os.path.exists(p):
            print(f"ERROR: DB not found: {p}", file=sys.stderr)
            return 2
    result = audit(di, dv)
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif as_oneline:
        print(_oneline(result))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
