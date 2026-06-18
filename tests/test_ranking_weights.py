"""Golden oracle for ranking weights — the W3 unification safety net.

Pins the EVIDENCE/SOURCE weight tables (constants/search/inject share one scale;
export_vault keeps a distinct richer one) and key compound_weight outputs, so a
dedup refactor cannot silently drift ranking. unittest.TestCase so it runs under
`python3 -m unittest discover` and pytest.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import constants  # noqa: E402
import assemble_context as ac  # noqa: E402
import search_impl as si  # noqa: E402
import export_vault as ev  # noqa: E402

GOLDEN_EVIDENCE = {"validated": 1.0, "observed": 0.7, "hypothesis": 0.4}
GOLDEN_SOURCE = {"user-explicit": 1.0, "agent-extracted": 0.5, "system-generated": 0.3, "imported": 0.3}


class RankingWeightsTest(unittest.TestCase):
    def test_constants_hold_the_canonical_values(self):
        self.assertEqual(constants.EVIDENCE_WEIGHTS, GOLDEN_EVIDENCE)
        self.assertEqual(constants.SOURCE_WEIGHTS, GOLDEN_SOURCE)

    def test_search_and_inject_agree_with_constants(self):
        for mod in (ac, si):
            self.assertEqual(mod.EVIDENCE_WEIGHTS, constants.EVIDENCE_WEIGHTS, mod.__name__)
            self.assertEqual(mod.SOURCE_WEIGHTS, constants.SOURCE_WEIGHTS, mod.__name__)

    def test_export_vault_uses_a_distinct_richer_curation_scale(self):
        # export_vault deliberately runs a finer scale (foundational tier,
        # user-implicit source); W3 must NOT fold it into the 3-tier constants.
        self.assertEqual(ev.EVIDENCE_WEIGHTS, {
            "foundational": 1.0, "validated": 0.9, "observed": 0.7,
            "hypothesis": 0.4, "system": 0.3,
        })
        self.assertEqual(ev.SOURCE_WEIGHTS, {
            "user-explicit": 1.0, "user-implicit": 0.8,
            "agent-extracted": 0.5, "system": 0.3,
        })
        self.assertNotEqual(ev.EVIDENCE_WEIGHTS, constants.EVIDENCE_WEIGHTS)

    def test_assemble_context_source_ratio_is_pinned(self):
        w_user = ac.compound_weight("validated", "user-explicit", None, drift_penalty=1.0, status="current")
        w_agent = ac.compound_weight("validated", "agent-extracted", None, drift_penalty=1.0, status="current")
        self.assertGreater(w_user, 0)
        self.assertAlmostEqual(w_agent, 0.5 * w_user)

    def test_imported_source_is_low_trust(self):
        # imported (Wave 1 importer, third-party) must rank below agent-extracted,
        # so an imported page never outranks our own session-validated knowledge.
        w_agent = ac.compound_weight("observed", "agent-extracted", None, drift_penalty=1.0, status="current")
        w_imported = ac.compound_weight("observed", "imported", None, drift_penalty=1.0, status="current")
        self.assertLess(w_imported, w_agent)
        self.assertAlmostEqual(w_imported, (0.3 / 0.5) * w_agent)

    def test_export_vault_default_weight_is_observed_times_user_explicit(self):
        # Empty meta + unknown path → get_meta_field supplies observed/user-explicit
        # defaults, so the effective default weight is 0.7 * 1.0 = 0.7 (the audit's
        # "0.5/0.5 → 0.25" was wrong for the common path).
        self.assertAlmostEqual(ev.compound_weight({}, "no/such/path", {}, body=None), 0.7)


if __name__ == "__main__":
    unittest.main()
