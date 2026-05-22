"""Eidetic shared constants. Single source of truth for cross-file values."""

EVIDENCE_WEIGHTS = {"validated": 1.0, "observed": 0.7, "hypothesis": 0.4}
SOURCE_WEIGHTS = {"user-explicit": 1.0, "agent-extracted": 0.5, "system-generated": 0.3}

DRIFT_PENALTIES = {
    "broken_wikilink": 0.8,
    "age_stale": 0.5,
    "confidence_escalation": 0.3,
}
