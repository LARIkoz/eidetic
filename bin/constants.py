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
    # STEP-1B confidence (§4). Reader-safe: DEFAULT 0.7 == the base-schema default
    # and the neutral cold-start, and no file re-read is needed — the writer
    # recomputes the fold on its next index pass. WITHOUT this, a pre-1B index
    # (table created before the confidence column existed; CREATE TABLE IF NOT
    # EXISTS never adds it) crashes every reader that SELECTs c.confidence
    # (assemble_context, search) with "no such column: c.confidence" the moment
    # it upgrades to v6 — confidence had NO migration, only the base CREATE.
    # conf_w ranking stays dark, so the interim 0.7 changes no ranking.
    "confidence": "ALTER TABLE memory_chunks ADD COLUMN confidence REAL DEFAULT 0.7",
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
# AC-1b confirmer FP (RE-HARDENED per AUDIT M1-1/M1-2). The confirmer is
# m1_contradiction.production_confirmer — a deterministic MINIMAL-PAIR opposition
# detector (antonym / mutually-exclusive-set / negation-asymmetry / numeric-slot),
# where the opposing tokens must occupy the SAME predicate slot of an otherwise-
# shared statement, version/temporal/date numbers are treated as UPDATES (never a
# contradiction), and a negation that cancels an antonym ("not required" ==
# "optional") is suppressed. Measured on the REALISTIC negative classes the audit
# named as poison — version bump, temporal/date/size updates, compatible neg-cancel,
# agreeing paraphrase, before/after-different-action, two-facts-both-true, add/remove
# changelog, near-duplicate, same-number-different-units, topic-adjacent — plus 8
# true contradictions: confirmer RECALL 8/8, FALSE-POSITIVES 0/14 (test
# test_production_confirmer_recall_and_zero_fp_realistic, both legs). NOTE the
# EARLIER turn-2 "0/7" number was measured on EASY negatives and is superseded.
# Candidate-gate recall (turn-2): 4/4 @ 0.58. The english floor is the documented
# lower band, not yet calibrated (no english-profile box on this host).
#
# ACTIVATION IS GATED: even with both numbers, M1 stays DORMANT (diagnostic-only,
# writes NO contradicted event) until EIDETIC_M1_CONTRADICTION is explicitly set —
# a switch DECOUPLED from the shared EIDETIC_CONFIDENCE_EVENTS rail (AUDIT M1-1
# remedy c). Flip it only after re-measuring FP on the deployment's own negatives.
M1_NEIGHBORS = 8
M1_CANDIDATE_MIN = {"multilingual": 0.58, "english": 0.38}
M1_CANDIDATE_MIN_DEFAULT = 0.58

# M2 multi-page synthesis (spec-m2-synthesis FR-1). M2_FANOUT = the bounded number
# of related pages an ingest may touch (≤ Karpathy's 10–15, blast-radius limited).
# M2_RELATED_MIN = the profile-aware cosine floor for the EDIT set — a DISTINCT
# constant, calibrated PRECISION-first (spec §8 Breaks-when: a loose floor spreads
# noise edits). It sits ABOVE M1's recall-oriented contradiction-candidate gate
# (0.58) because a synthesis EDIT is higher-consequence than a contradiction
# CANDIDATE (M1 has the fail-closed confirmer behind it; an M2 edit mutates page
# bytes), and above e5-large's ~0.69–0.72 unrelated-noise band (M1 AC-1b evidence)
# so weakly-related pages are never edited. Unknown profile → the stricter end.
#
# AC calibration (Leg A, profile=multilingual, e5-large): labeled related vs
# unrelated card pairs (query→passage). Measured: related cos 0.769–0.803 (min
# 0.769), unrelated cos 0.625–0.741 (max 0.741). The 0.78 floor sits in the
# separation window → FALSE-ADMIT 0/4 (precision-first; M2 has no confirmer gating
# the EDIT itself, so this floor IS the edit precision gate) with related recall
# 3/4 (the one 0.769 near-miss is a distant paraphrase — a missed synthesis is
# safe, a noise edit is not). english floor uncalibrated (no english-profile host).
M2_FANOUT = 8
M2_RELATED_MIN = {"multilingual": 0.78, "english": 0.62}
M2_RELATED_MIN_DEFAULT = 0.78

# M2.1 relevance gate (spec-m2-synthesis value fix). The bi-encoder cosine floor
# (M2_RELATED_MIN) is a cheap RECALL gate — e5-large's cosine band is high+narrow,
# so short, stylistically-similar-but-UNRELATED cards score spuriously high (real
# dogfood: `feedback-no-clutter…` ↔ `never-suggest-topping-up…` cosine 0.868, yet
# semantically unrelated). M1 is clean because a cross-encoder confirms; M2 had none
# — that asymmetry was the bug. M2_RELEVANCE_MIN is the SECOND gate: the S5
# cross-encoder (jina-reranker-v2) relevance logit, checked on the EDIT before M2
# touches a page. FAIL-CLOSED in code: no reranker / None / below floor ⇒ NO edit
# (never cosine-only). Profile-aware like M2_RELATED_MIN.
#
# M2CAL calibration (owner's box, REAL 1390-card store, read-only dry-run). The
# earlier 0.0 floor was an UNCALIBRATED placeholder: it was chosen when the reranker
# was unprovisioned on the build box (ONNX missing) so no real logit distribution
# existed. The M2-activation dry-run over the real store MEASURED that distribution
# for the first time: rel min=-3.34, median=-0.20, max=+1.38. A 0.0 floor therefore
# admits the entire top ~third of neighbors INCLUDING junk — e.g. the semantically-
# unrelated pair `github-anchor-slugify`↔`key-hunt` scores rel=-2.9 (correctly deep
# negative) but many noise pairs sit in [0.0, 0.75) and would pass. TRUE relations
# score ≥0.75 (measured: two obd-seo builder projects at rel=1.05). Because median is
# NEGATIVE, ≥0.75 is well into the right tail — precision-first, as an M2 edit mutates
# page bytes and there is no confirmer behind it (this floor IS the edit-precision
# gate). Raising 0.0 → 0.75 is the core M2CAL over-fire fix (the dry-run projected
# ~2845 synthesis edits at 0.0, 60% of them cross-project). Do NOT lower without
# re-measuring the reranker logit distribution on the deployment's own store.
M2_RELEVANCE_MIN = {"multilingual": 0.75, "english": 0.75}
M2_RELEVANCE_MIN_DEFAULT = 0.75

# M2CAL supersession bar (spec-m2-synthesis over-fire fix, change #3). The auto-
# supersession path (marking a card obsolete via frontmatter `superseded_by` + a
# terminal `contradicted` event) is DESTRUCTIVE and measured ~100% FALSE-positive on
# the real store (every sampled same-project supersession was two DISTINCT docs, not
# an evolution of one). It is therefore SUGGESTION-ONLY by default and only auto-
# applies behind the explicit EIDETIC_M2_AUTOSUPERSEDE flag AND above this STRICT
# relevance bar — set to 1.0 (stricter than the 0.75 synthesis floor) because
# retiring a page is higher-consequence than annotating one: only a pair the cross-
# encoder scores at/above the very top of the measured band (max +1.38) may auto-
# retire. Reuses the same S5 reranker logit as M2_RELEVANCE_MIN.
M2_SUPERSEDE_MIN = 1.0

# M3 auto-file (spec-m3-autofile FR-1/FR-2). M3 files a recalled answer back as a
# typed page ONLY through a claim-support gate with teeth, at agent cold-start.
#
# M3_NEIGHBORS = K vector-neighbors probed to detect a near-duplicate before
# filing (same primitive as M1/M2).
#
# M3_DEDUP_MIN = the profile-aware cosine floor above which the answer is a
# NEAR-DUPLICATE of an existing page → route to M2 (update), file NO new page
# (FR-1, AC-4). Calibrated AT the compound near-duplicate line
# (VECTOR_GATE_MIN_SIM_BY_PROFILE = {multilingual 0.85, english 0.60}, ~50 lines
# above): a genuine same-topic duplicate scored cos 0.920 while e5 topical noise
# sat ~0.83-0.84 (compound's build note), so 0.85 cleanly admits real duplicates
# to M2 and lets a genuinely-new topic file. Dedup is vector-first: an FTS-only
# store returns no neighbors, so M3 files a paraphrase there (stated honestly,
# Breaks-when). Unknown profile → the stricter (multilingual) end.
#
# M3_SUPPORT_MIN = the claim-support floor for the DEFAULT deterministic
# span-overlap scorer (LLM-free; the fraction of a claim's content words covered
# by its best cited span, ∈[0,1]). ANY material claim below this ⇒ the WHOLE
# answer is REJECTED, no page, no event (FR-2, AC-1). Precision-first / bias
# toward REJECT (NFR): a rejected good answer is recoverable (a human can still
# promote); a filed UNSUPPORTED answer is the top laundering risk. 0.5 requires a
# majority of a claim's content words to be span-backed. A registered
# cross-encoder scorer supplies its own floor via the injection seam.
M3_NEIGHBORS = 8
M3_DEDUP_MIN = {"multilingual": 0.85, "english": 0.60}
M3_DEDUP_MIN_DEFAULT = 0.85
M3_SUPPORT_MIN = 0.5
