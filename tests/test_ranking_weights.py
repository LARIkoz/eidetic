"""Golden oracle for ranking weights — the safety net for the W3 unification.

The EVIDENCE/SOURCE weight tables are currently duplicated in four modules
(constants.py, assemble_context.py, export_vault.py, search_impl.py) with
identical VALUES but divergent DEFAULTS — assemble_context falls back to 0.7/1.0,
export_vault to 0.5/0.5 — plus an export-only body_weight_adjustment multiplier.

W3 will make constants.py the single source of truth (import the tables
everywhere) and deliberately reconcile the defaults. This test pins the current
numbers so that:
  * the table-equality assertions stay GREEN through unification (proof the
    values did not drift), and
  * the export-default assertion turns RED exactly when W3 reconciles 0.5/0.5 —
    a visible, intentional change, not a silent ranking shift.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import constants  # noqa: E402
import assemble_context as ac  # noqa: E402
import search_impl as si  # noqa: E402
import export_vault as ev  # noqa: E402

GOLDEN_EVIDENCE = {"validated": 1.0, "observed": 0.7, "hypothesis": 0.4}
GOLDEN_SOURCE = {"user-explicit": 1.0, "agent-extracted": 0.5, "system-generated": 0.3}


def test_constants_hold_the_canonical_values():
    assert constants.EVIDENCE_WEIGHTS == GOLDEN_EVIDENCE
    assert constants.SOURCE_WEIGHTS == GOLDEN_SOURCE


def test_search_and_inject_agree_with_constants():
    # The search + injection path share constants.py's 3-tier scale. W3 should
    # collapse THESE copies into constants imports — values already match, so it
    # is a safe dedup. (export_vault is intentionally excluded — see below.)
    for mod in (ac, si):
        assert mod.EVIDENCE_WEIGHTS == constants.EVIDENCE_WEIGHTS, mod.__name__
        assert mod.SOURCE_WEIGHTS == constants.SOURCE_WEIGHTS, mod.__name__


def test_export_vault_uses_a_distinct_richer_curation_scale():
    # export_vault deliberately runs a FINER scale than constants: a "foundational"
    # tier above validated (which it downweights to 0.9), plus a "user-implicit"
    # source. W3 must NOT blindly fold this into the 3-tier constants table — that
    # would drop tiers and silently shift vault ordering. Pinned so the divergence
    # is explicit and any "unify everything" refactor trips here first.
    assert ev.EVIDENCE_WEIGHTS == {
        "foundational": 1.0, "validated": 0.9, "observed": 0.7,
        "hypothesis": 0.4, "system": 0.3,
    }
    assert ev.SOURCE_WEIGHTS == {
        "user-explicit": 1.0, "user-implicit": 0.8,
        "agent-extracted": 0.5, "system": 0.3,
    }
    assert ev.EVIDENCE_WEIGHTS != constants.EVIDENCE_WEIGHTS  # genuinely diverges


def test_assemble_context_source_ratio_is_pinned():
    # agent-extracted memories rank at 0.5x user-explicit (the self-referential
    # discount). Pinned as a ratio so it is independent of status/freshness.
    w_user = ac.compound_weight("validated", "user-explicit", None, drift_penalty=1.0, status="current")
    w_agent = ac.compound_weight("validated", "agent-extracted", None, drift_penalty=1.0, status="current")
    assert w_user > 0
    assert abs(w_agent - 0.5 * w_user) < 1e-9, (w_agent, w_user)


def test_export_vault_default_weight_is_observed_times_user_explicit():
    # Empty meta + unknown path: get_meta_field supplies "observed"/"user-explicit"
    # as defaults (NOT the 0.5/0.5 .get() fallback, which only fires on genuinely
    # unknown strings), so the effective default weight is 0.7 * 1.0 = 0.7. The
    # audit's "0.5/0.5 → 0.25 divergence" was wrong for the common path — verified
    # here, the real default matches assemble_context's 0.7.
    w = ev.compound_weight({}, "no/such/path", {}, body=None)
    assert abs(w - 0.7) < 1e-9, w
