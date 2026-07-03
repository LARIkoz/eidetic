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
