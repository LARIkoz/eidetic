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

# SINGLE SOURCE of the memory_chunks derived/relation-column migrations, shared
# by BOTH schema paths: the WRITER (index_impl.migrate_schema, applied to
# index.db at init) and the READER (search_impl.ensure_agent_columns, applied
# defensively when searching a possibly-older index). The two used to keep
# separate hand-maintained lists that DRIFTED — the reader was missing the
# `*_explicit` columns while the writer was missing `project` — so a column
# added on one path silently did not exist on the other. Keep every column an
# older DB might lack here, ordered as added; both paths iterate this dict and
# skip columns that already exist (duplicate-column errors are swallowed).
MEMORY_CHUNK_MIGRATIONS = {
    "project": "ALTER TABLE memory_chunks ADD COLUMN project TEXT DEFAULT ''",
    "card_kind": "ALTER TABLE memory_chunks ADD COLUMN card_kind TEXT DEFAULT ''",
    "status": "ALTER TABLE memory_chunks ADD COLUMN status TEXT DEFAULT 'current'",
    "area": "ALTER TABLE memory_chunks ADD COLUMN area TEXT DEFAULT ''",
    "supersedes": "ALTER TABLE memory_chunks ADD COLUMN supersedes TEXT DEFAULT ''",
    "superseded_by": "ALTER TABLE memory_chunks ADD COLUMN superseded_by TEXT DEFAULT ''",
    "contradicts": "ALTER TABLE memory_chunks ADD COLUMN contradicts TEXT DEFAULT ''",
    "contradicted_by": "ALTER TABLE memory_chunks ADD COLUMN contradicted_by TEXT DEFAULT ''",
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
}

# Columns whose ADDITION requires re-reading every file (the writer path only):
# pre-upgrade rows cannot distinguish own-frontmatter values from previously
# propagated ones, so the source values must be reloaded from the files.
RELATION_EXPLICIT_COLUMNS = {"superseded_by_explicit", "contradicted_by_explicit"}
FORCED_REREAD_ON_ADD = RELATION_EXPLICIT_COLUMNS | {"status_explicit"}
