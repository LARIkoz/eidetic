"""Eidetic shared constants. Single source of truth for cross-file values."""

EVIDENCE_WEIGHTS = {"validated": 1.0, "observed": 0.7, "hypothesis": 0.4}
# "imported" = third-party content filed by the importer (Wave 1). Low-trust:
# below agent-extracted, so imported pages never outrank our own session-validated
# knowledge or user feedback. Tied with system-generated at the floor.
SOURCE_WEIGHTS = {"user-explicit": 1.0, "agent-extracted": 0.5, "system-generated": 0.3, "imported": 0.3}

DRIFT_PENALTIES = {
    "broken_wikilink": 0.8,
    "age_stale": 0.5,
    "confidence_escalation": 0.3,
    # Truth-maintenance slice (v6 preview): a card DECLARED contradicted
    # (frontmatter `contradicted_by:` on the card, or `contradicts:` on the
    # contradicting card, propagated at index time) ranks below its
    # contradictor. Semantic auto-DETECTION of contradictions remains v6.
    "contradicted": 0.4,
    # Diagnostics-only findings (penalty 1.0 = never changes ranking):
    # a declared `contradicts:`/`supersedes:` whose target resolves to no
    # card in the declarer's project (typo / deleted / unqualified
    # cross-project ref) — surfaced on the DECLARER so the dead declaration
    # gets fixed instead of silently no-opping;
    "unresolved_relation": 1.0,
    # a declaration REFUSED by the authority gate (declarer older or of a
    # lower source-tier than its target) — surfaced on the TARGET so the
    # dispute is visible, but a low-trust card can never down-rank a
    # canonical one.
    "relation_claim": 1.0,
}

# Declared relations are facts the writer asserted, not heuristic detections —
# they penalize from the FIRST drift run, bypassing the `first_seen > 1`
# grace gate that protects against transient mis-detections.
DECLARED_DRIFT_TYPES = {"contradicted"}

# SINGLE SOURCE of the memory_chunks derived/relation-column migrations, split
# by who may safely ADD a column (audit F1 — reader-first back-fill defeat).
#
# READER_SAFE_MIGRATIONS — columns any code (writer OR a read-only search) may
# add on an old index: their DEFAULT is a correct value with no back-fill from
# frontmatter needed ('' / 'current').
#
# WRITER_BACKFILL_MIGRATIONS — columns that store the card's OWN frontmatter
# value and CANNOT be reconstructed from a DEFAULT: adding one with DEFAULT ''
# silently means "the file declared nothing", so it MUST be paired with a forced
# file re-read. Only the WRITER (which then reindexes) may add these — a reader
# that added them (with no re-read) would defeat the back-fill and let
# propagation clear a deliberate superseded/archived demotion. A reader that
# meets a DB lacking these tolerates their absence (it never SELECTs them).
READER_SAFE_MIGRATIONS = {
    "project": "ALTER TABLE memory_chunks ADD COLUMN project TEXT DEFAULT ''",
    "card_kind": "ALTER TABLE memory_chunks ADD COLUMN card_kind TEXT DEFAULT ''",
    "status": "ALTER TABLE memory_chunks ADD COLUMN status TEXT DEFAULT 'current'",
    "area": "ALTER TABLE memory_chunks ADD COLUMN area TEXT DEFAULT ''",
    "supersedes": "ALTER TABLE memory_chunks ADD COLUMN supersedes TEXT DEFAULT ''",
    "superseded_by": "ALTER TABLE memory_chunks ADD COLUMN superseded_by TEXT DEFAULT ''",
    "contradicts": "ALTER TABLE memory_chunks ADD COLUMN contradicts TEXT DEFAULT ''",
    "contradicted_by": "ALTER TABLE memory_chunks ADD COLUMN contradicted_by TEXT DEFAULT ''",
}
WRITER_BACKFILL_MIGRATIONS = {
    # The card's OWN frontmatter relation value, kept apart from the effective
    # column so authoritative re-propagation can distinguish "the file says so"
    # from "another card's declaration was pushed here" and clear the latter
    # when the declarer disappears.
    "superseded_by_explicit": "ALTER TABLE memory_chunks ADD COLUMN superseded_by_explicit TEXT DEFAULT ''",
    "contradicted_by_explicit": "ALTER TABLE memory_chunks ADD COLUMN contradicted_by_explicit TEXT DEFAULT ''",
    # The card's OWN frontmatter `status:` (lower-cased/stripped, '' if none).
    # The `status` column is DERIVED — 'superseded' whenever the effective
    # superseded_by (own OR propagated from another card's `supersedes:`) is set,
    # else the explicit status, else 'current'. Storing the explicit value lets
    # propagation recompute the derived status each reindex WITHOUT clobbering a
    # project-authored status (archived/deprecated/…).
    "status_explicit": "ALTER TABLE memory_chunks ADD COLUMN status_explicit TEXT DEFAULT ''",
    # STEP 1B — the derived managed/exempt lifecycle label (§2.3). Derived from
    # type/source/card_kind + materialized at index time; a re-read populates it.
    "lifecycle": "ALTER TABLE memory_chunks ADD COLUMN lifecycle TEXT DEFAULT ''",
}
# The WRITER's full migration set (reader-safe first, then back-fill columns).
MEMORY_CHUNK_MIGRATIONS = {**READER_SAFE_MIGRATIONS, **WRITER_BACKFILL_MIGRATIONS}

# Columns whose ADDITION requires re-reading every file (the writer path only):
# pre-upgrade rows cannot distinguish own-frontmatter values from previously
# propagated ones, so the source values must be reloaded from the files. Equal
# to the writer-back-fill set by construction.
RELATION_EXPLICIT_COLUMNS = {"superseded_by_explicit", "contradicted_by_explicit"}
FORCED_REREAD_ON_ADD = set(WRITER_BACKFILL_MIGRATIONS)

# Size-aware recursive chunking (spec-chunker FR-6). A markdown section (at any
# heading level) larger than this is split further — H2→H3→H4 recursively, then
# paragraph-window fallback — so a catalog "monster" section becomes precise
# per-field chunks instead of one 59 KB blob. Chosen so typical memory-card
# sections (< 4 KB) never split (zero-churn, FR-4) while catalog monsters die.
# A FIXED constant (not env): chunk determinism per store, no config surface.
MAX_CHUNK_CHARS = 6000

# M1 semantic contradiction detection (spec-m1-contradiction FR-1, FR-2). K
# vector-neighbors probed per ingested card, and the candidate-gate cosine floor
# — a DISTINCT constant, DECOUPLED from compound's near-duplicate line (0.85/0.60)
# and deliberately RECALL-oriented (below the duplicate line), because a
# contradiction ("same entity, opposite claim") lands in a moderate-similarity
# band; the fail-closed confirmer (FR-3) provides precision. Profile-aware (S3);
# unknown profile → the stricter (multilingual) end.
#
# AC-1b build calibration (Leg A, profile=multilingual, e5-large): on a labeled
# contradiction set (Postgres↔MySQL, enabled↔disabled, 3↔10 retries, expiry↔never)
# query→passage cosine sat at 0.80–0.85 → RECALL 4/4 admitted at the 0.58 floor.
# e5-large compresses short-sentence cosine into a high, narrow band (even
# unrelated controls scored 0.69–0.72), so the gate is deliberately PERMISSIVE:
# it drops only the far tail, the top-K=8 neighbor bound caps confirmer cost, and
# the confirmer (FR-3) owns precision.
#
# AC-1b confirmer FP (turn-2, m1_contradiction.production_confirmer — a
# deterministic shared-frame opposition detector: antonym / mutually-exclusive-set
# / negation-asymmetry / numeric-slot): on a labeled set of 6 contradictions
# (Postgres↔MySQL, enabled↔disabled, 3↔10 retries, expire↔never, sync↔async,
# allow↔deny) and 7 non-contradictions (4 related-non-conflicting + 3 unrelated
# controls in the 0.69–0.72 band) → confirmer RECALL 6/6, FALSE-POSITIVES 0/7
# (FP-rate 0.000). Both halves of the owner-signed F4 calibration now hold:
# candidate-gate recall 4/4 @ 0.58, confirmer FP 0.000. The english floor is the
# documented lower band, not yet calibrated (no english-profile box on this host).
M1_NEIGHBORS = 8
M1_CANDIDATE_MIN = {"multilingual": 0.58, "english": 0.38}
M1_CANDIDATE_MIN_DEFAULT = 0.58
