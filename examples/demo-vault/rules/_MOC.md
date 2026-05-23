# Rules

> Behavioral rules and feedback memories — what to do, what to avoid, why.

## Pipeline reliability

- [[silent-failures-are-not-ok]] — surfacing silent failures with loud-failure guardrails
- [[deep-research-all-tools-required]] — never synthesize on partial research coverage
- [[validate-agent-findings]] — verify agent diagnostic findings before implementing
- [[handoff-requires-4-pass-verification]] — 4-pass framework catches structural issues
- [[model-freshness-check]] — verify model availability before use; configs go stale

## Consilium / multi-voice review

- [[consilium-redteam-mandatory]] — single-round consilium = groupthink; adversarial round catches it
- [[consilium-4-tier-postprocessing]] — BLOCKER/IMPORTANT/VERIFY-DEEPER/NOISE classification
- [[consilium-synth-hallucinations]] — synth invents global claims; read AUDIT and REDTEAM first

## Smoke testing & verification

- [[smoke-test-incrementally]] — small smoke tests on every hypothesis before full runs
- [[dual-smoke-tests]] — Direct + RED Team rounds before any destructive change
- [[save-raw-data-from-audits]] — preserve raw outputs, not just summaries
- [[pmid-hallucination]] — verify every PubMed citation; ~60% are hallucinated

## DB & data

- [[db-operations-backup-locking]] — SQLite source of truth, backup, WAL locking, bulk writes
- [[bulk-sqlite-ram-disk]] — RAM disk for 10M+ row imports
- [[ledger-journal-pattern]] — LEDGER + IMPORT_JOURNAL for cross-session data tracking
- [[parallel-batch-llm]] — 30 items/batch × 6 workers = 100× sequential

## Scraping & tooling

- [[cdp-contention-one-scraper-per-tab]] — parallel CDP scripts on same tab cause ~50% errors
- [[exa-over-webfetch]] — Exa for JS-rendered pages, WebFetch only for static
- [[research-tool-shape-match]] — verify task shape, not trigger keywords, before firing research

## Security

- [[litellm-supply-chain-attack]] — supply chain attack via litellm; lessons + audit checklist
- [[api-key-not-in-inline-python]] — env vars don't propagate to `python3 -c` subprocess

## Decision style & calibration

- [[decide-from-context]] — search skills / keys.env / memory before asking the user
- [[defend-correct-answer]] — don't flip-flop on a correct answer just because user reasks
- [[confidence-escalation-drift-signal]] — raising confidence without new data = recalibration signal
- [[subagent-full-toolkit]] — explicitly list all tools in subagent prompts

## Brainstorm & explanation style

- [[brainstorm-leading-questions]] — Socratic mode when user is stuck
- [[explain-multi-vector-topics-block-at-a-time]] — 3+ vectors → numbered blocks, checkpoints
- [[formatting-readability]] — short paragraphs, bold only on key terms, no wall-of-text

## Distribution

- [[seo-geo-for-public-repos]] — SEO + GEO + stars strategy for every public repo

## Cross-cutting

- [[skills-vs-memory]] — knowledge about tools → SKILL.md; memory = project context
- [[think-in-english]] — internal reasoning, plans, todos in English regardless of user language
