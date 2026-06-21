#!/usr/bin/env python3
"""Eidetic doctor canary — FUNCTIONAL proof of embed -> vector -> search + usage logging.

The doctor's other checks are STRUCTURAL: they count chunks/vectors, confirm files
exist, and verify vector ALIGNMENT (chunk_id+hash join). All of those pass even when
the embedder is silently broken — a wrong model, a pooling change between index-time
and now, or an evicted weight cache produces vectors that JOIN fine but no longer mean
anything. This canary EXERCISES the chain end-to-end so that class of break fails LOUD:

  §3.1 embed a real indexed card's own name through the LIVE model -> vector search ->
       assert that same card comes back at rank <= 3. Proves the model loads, emits a
       valid vector, the chunk_id/hash join works, and ranking is sane. A pooling/model
       drift puts the query vector in a different space than the stored passages, so the
       card no longer self-retrieves -> caught (the exact mean-pooling-warning class).

  §3.2 run a real confident search through search_impl and confirm the usage logger
       FIRED — written to a TEMP log via EIDETIC_USAGE_LOG_PATH, never the prod usage.log,
       so a health check can't inflate one card's surfacing count and poison the v5.6.0
       dead-card / top-used telemetry it is meant to verify.

Fail-soft: no fastembed -> skip §3.1 (FTS-only mode is valid); no usage module -> note.
The pure functions take injectable callables so the logic is unit-tested without loading
the ~2 GB e5 model (tests/test_canary.py).

Output: shell-evalable KEY='value' lines for doctor.sh (values shlex-quoted).
"""

import os
import shlex
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MIN_NAME_LEN = 20   # pick a distinctive card name so a healthy embedder self-retrieves reliably
TOP_OK = 3          # rank <= this = healthy (the AC1 bar)
FETCH_K = 10        # fetch this many to grade the rank (4..FETCH_K = degraded warn, absent = fail)


# --------------------------------------------------------------- §3.1 embed canary
def _fastembed_available():
    try:
        import fastembed  # noqa: F401
        return True
    except Exception:
        return False


def _default_search(vectors_db, query, limit):
    import embed
    return embed.search(vectors_db, query, limit=limit)


def pick_canary_card(vectors_db, conn=None):
    """Deterministic, distinctive card that HAS a vector: smallest chunk_id whose
    name is long enough to self-retrieve; fall back to the longest available name,
    then any non-empty name. Returns (chunk_id, name) or None when there are no
    usable vectors."""
    import sqlite3
    own = conn is None
    if own:
        try:
            conn = sqlite3.connect(f"file:{vectors_db}?mode=ro", uri=True)
        except sqlite3.Error:
            return None
    try:
        row = conn.execute(
            "SELECT chunk_id, name FROM vectors "
            "WHERE name IS NOT NULL AND length(name) >= ? ORDER BY chunk_id LIMIT 1",
            (MIN_NAME_LEN,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT chunk_id, name FROM vectors "
                "WHERE name IS NOT NULL AND length(name) > 0 "
                "ORDER BY length(name) DESC, chunk_id LIMIT 1"
            ).fetchone()
        return (row[0], row[1]) if row else None
    except sqlite3.Error:
        return None
    finally:
        if own:
            conn.close()


def embed_canary(index_db, vectors_db, search_fn=None, require_fastembed=True):
    """Embed a real card's name -> vector search -> grade where that card ranks.

    search_fn(vectors_db, query, limit) -> list of (sim, chunk_id, ...) rows
    (embed.search's shape). Injected in tests to avoid loading the model."""
    if search_fn is None:
        if not os.path.exists(vectors_db):
            return {"status": "skip", "detail": "no vectors.db yet — vector canary skipped (FTS still works)"}
        if require_fastembed and not _fastembed_available():
            return {"status": "skip", "detail": "fastembed not importable — FTS-only mode (vector canary skipped)"}
        search_fn = _default_search
    card = pick_canary_card(vectors_db)
    if card is None:
        return {"status": "skip", "detail": "no vectors built yet — nothing to canary"}
    cid, name = card
    try:
        results = search_fn(vectors_db, name, FETCH_K)
    except Exception as e:  # model load / numpy / db error
        return {"status": "fail", "card": name,
                "detail": f"embed+vector search raised: {type(e).__name__}: {e}"}
    if not results:
        return {"status": "fail", "card": name,
                "detail": "vector search returned 0 results — model/dim drift, dead cache, or empty store"}
    ids = [r[1] for r in results]
    rank = ids.index(cid) + 1 if cid in ids else None
    if rank and rank <= TOP_OK:
        return {"status": "ok", "card": name, "rank": rank,
                "detail": f"card self-retrieved at rank {rank}/{len(results)} (embed+vector+search functional)"}
    if rank:
        return {"status": "warn", "card": name, "rank": rank,
                "detail": f"card self-retrieved at rank {rank} (> {TOP_OK}) — embedder may be degrading"}
    return {"status": "fail", "card": name,
            "detail": f"canary card NOT in top {FETCH_K} — embedder broken (wrong model / pooling drift / dead cache)"}


# --------------------------------------------------------------- §3.2 usage canary
def _load_usage():
    import importlib.util
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage.py")
        spec = importlib.util.spec_from_file_location("eidetic_usage_canary", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _default_run_search(db_path, query):
    """The REAL deployed read path: _run_query -> _log_usage (exactly what a user
    search triggers), so a broken wiring between search and the logger is caught."""
    import search_impl
    results = search_impl._run_query(db_path, query, 5, None, warn=False)
    search_impl._log_usage(results, query, db_path)


def _default_log_probe(usage_mod, db_path):
    """Direct logger probe with a synthetic confident result — proves usage.py's
    writer works even when the canary query itself was not confident enough to
    trigger logging through search."""
    usage_mod.log_surfaced(
        [{"path": "__canary__", "section": "", "confidence": "high"}],
        "__eidetic_canary__", db_path, "high",
    )


def usage_canary(db_path, probe_query, usage_mod=None, run_search=None, log_probe=None):
    """Confirm the usage logger FIRES. Writes only to a temp log (never prod).
    States: off | notdeployed | live | silent."""
    if os.environ.get("EIDETIC_USAGE_LOG", "on").strip().lower() == "off":
        return {"status": "off", "detail": "EIDETIC_USAGE_LOG=off — usage tracking opted out"}
    if usage_mod is None:
        usage_mod = _load_usage()
    if usage_mod is None:
        return {"status": "notdeployed", "detail": "usage.py not importable — telemetry not deployed"}
    run_search = run_search or _default_run_search
    log_probe = log_probe or _default_log_probe

    fd, tmp = tempfile.mkstemp(prefix="eidetic-usage-canary-", suffix=".log")
    os.close(fd)
    os.remove(tmp)  # let the logger create it; mkstemp only reserves the name
    prev = os.environ.get("EIDETIC_USAGE_LOG_PATH")
    os.environ["EIDETIC_USAGE_LOG_PATH"] = tmp

    def _grew():
        try:
            return os.path.exists(tmp) and os.path.getsize(tmp) > 0
        except OSError:
            return False

    try:
        try:
            run_search(db_path, probe_query)
        except Exception:
            pass  # fall through to the direct probe
        if _grew():
            return {"status": "live", "detail": "logger fired on a real confident search (search -> usage.log wiring works)"}
        try:
            log_probe(usage_mod, db_path)
        except Exception:
            pass
        if _grew():
            return {"status": "live", "detail": "logger writes (direct probe); the canary query was not confident enough to trigger via search"}
        return {"status": "silent", "detail": "usage logger deployed but recorded NOTHING — wiring or writer may be broken"}
    finally:
        if prev is None:
            os.environ.pop("EIDETIC_USAGE_LOG_PATH", None)
        else:
            os.environ["EIDETIC_USAGE_LOG_PATH"] = prev
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


# --------------------------------------------------------------- §3.6 translate canary
import re as _re

# A LONG, unambiguously-Russian sentence: the apple backend auto-detects the source
# language (NLLanguageRecognizer), and a short phrase like "привет мир" mis-detects as
# Kazakh → no pack → empty (a false-positive). A full sentence detects as ru reliably.
TRANSLATE_PROBE = "Память дрейфует со временем"  # → "Memory drifts with time"


def _default_translate(query, target, backend):
    import translate
    return translate.translate(query, target, backend)


def translate_canary(translate_fn=None, backend=None, configured=None):
    """FUNCTIONALLY test the translator (parallel to the embed canary): translate a
    fixed RU probe and assert the result is non-empty, CHANGED, and Cyrillic-free.
    The doctor otherwise only shows backend AVAILABILITY (resolves? pack installed?) —
    which is "is it present", not "does it actually translate". Skips cleanly when
    translation is OFF (the default) or no backend is available."""
    if translate_fn is None:
        try:
            import translate as _t
            cfg = configured or _t.active_backend()
            if cfg == "off":
                return {"status": "off", "detail": "query translation OFF (default) — translator not functionally tested"}
            backend = backend or _t.resolve_backend(cfg)
            if not backend:
                return {"status": "skip", "detail": f"'{cfg}' set but no translation backend available"}
            translate_fn = _default_translate
        except Exception as e:
            return {"status": "skip", "detail": f"translate module unavailable: {type(e).__name__}"}
    try:
        out = translate_fn(TRANSLATE_PROBE, "en", backend)
    except Exception as e:
        return {"status": "fail", "backend": backend, "detail": f"translator ({backend}) raised: {type(e).__name__}: {e}"}
    if not out or not out.strip():
        return {"status": "fail", "backend": backend, "detail": f"translator ({backend}) returned EMPTY for '{TRANSLATE_PROBE}'"}
    if out.strip().lower() == TRANSLATE_PROBE.lower():
        return {"status": "fail", "backend": backend, "detail": f"translator ({backend}) returned the input UNCHANGED"}
    if _re.search(r"[А-Яа-яЁё]", out):
        return {"status": "warn", "backend": backend, "detail": f"translator ({backend}) output still has Cyrillic: '{out.strip()[:40]}'"}
    return {"status": "ok", "backend": backend, "detail": f"'{TRANSLATE_PROBE}' -> '{out.strip()[:40]}' ({backend} functional)"}


# --------------------------------------------------------------- CLI for doctor.sh
def _emit(prefix, d):
    print(f"{prefix}_STATUS={shlex.quote(str(d.get('status', '')))}")
    print(f"{prefix}_DETAIL={shlex.quote(str(d.get('detail', '')))}")
    if "rank" in d and d["rank"] is not None:
        print(f"{prefix}_RANK={shlex.quote(str(d['rank']))}")
    if "card" in d:
        print(f"{prefix}_CARD={shlex.quote(str(d['card']))}")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Eidetic doctor functional canary")
    default_base = os.path.expanduser(
        os.environ.get("EIDETIC_MEMORY_SYSTEM", "~/.claude/memory-system"))
    ap.add_argument("--index", default=os.path.join(default_base, "db", "index.db"))
    ap.add_argument("--vectors", default=os.path.join(default_base, "db", "vectors.db"))
    ap.add_argument("--db", default=None, help="index.db used for usage logging (defaults to --index)")
    args = ap.parse_args(argv)
    db = args.db or args.index

    emb = embed_canary(args.index, args.vectors)
    _emit("CANARY_EMBED", emb)
    # Use the embed canary's card name as the §3.2 search probe — a card's own name
    # is the most reliable way to produce a confident hit that should log.
    probe = emb.get("card") or "test"
    usg = usage_canary(db, probe)
    _emit("CANARY_USAGE", usg)
    tr = translate_canary()
    _emit("CANARY_TRANSLATE", tr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
