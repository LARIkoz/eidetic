# Eidetic Memory Schema

The contract every memory card follows. Karpathy's "LLM Wiki" insight: _the
schema is the maintenance contract_ — the LLM keeps the wiki consistent only if
the shape is explicit. This is that shape. Values here are the single source of
truth; they mirror `bin/constants.py` and `bin/drift_check.py`.

A card is one Markdown file: YAML frontmatter + body. One fact (or one
synthesized answer) per file.

## Frontmatter

```yaml
---
name: synthesis-eidetic-vs-claude-mem-6-moats # stable kebab slug == filename stem
description: "one-line searchable hook" # used for recall relevance + MOC
type: project # user | feedback | project | reference
card_kind: synthesis # optional; inferred if absent (see below)
evidence: observed # validated | observed | hypothesis
source: agent-extracted # user-explicit | agent-extracted | system-generated
last_verified: 2026-06-18 # YYYY-MM-DD; drives age drift
status: current # optional; inferred if absent
superseded_by: other-card-name # optional; sets status=superseded
---
```

`name` + `description` are required for recall. The rest default sanely.
Nested `metadata:` blocks (`metadata: { type: ... }`) are also accepted by the
indexer for back-compat, but flat keys are canonical.

## `type` — the 4 buckets

| type        | meaning                                | vault folder  | age drift |
| ----------- | -------------------------------------- | ------------- | --------- |
| `user`      | who the user is (profile, preferences) | `profile/`    | 180 d     |
| `feedback`  | how the agent should work (rules)      | `rules/`      | timeless  |
| `project`   | ongoing work, goals, findings          | `projects/`   | 30 d      |
| `reference` | pointers to external/durable knowledge | `references/` | 90 d      |

## `card_kind` — the knowledge type

Finer than `type`. **Explicit `card_kind:` always wins.** If absent it is
inferred from `type` + name/description terms (`bin/index_impl.py:infer_card_kind`).
Surfaced to the agent in injected context and used for per-kind age drift.

| card_kind   | set by                                      | age drift | notes                                     |
| ----------- | ------------------------------------------- | --------- | ----------------------------------------- |
| `rule`      | inferred (type=feedback)                    | timeless  | behavioral guidance                       |
| `profile`   | inferred (type=user)                        | 180 d     | user/partner profile                      |
| `code`      | inferred (type=code)                        | timeless¹ | code-index chunk                          |
| `handoff`   | inferred ("handoff")                        | 90 d      | session handoff                           |
| `todo`      | inferred                                    | 60 d      | next-session work                         |
| `status`    | inferred                                    | 60 d      | current-state snapshot                    |
| `bug`       | inferred                                    | 60 d      | bug / regression                          |
| `decision`  | inferred                                    | 90 d      | a decision made                           |
| `synthesis` | inferred ("synthesis") **or** `remember.py` | 120 d     | a filed-back answer / synthesis           |
| `research`  | inferred                                    | 90 d      | research finding                          |
| `concept`   | **explicit only**                           | 365 d     | methodology / framework (near-timeless)   |
| `entity`    | **explicit only**                           | 365 d     | a person / tool / product (near-timeless) |
| `reference` | inferred                                    | 90 d      | external pointer                          |
| `finding`   | inferred (fallback)                         | 90 d      | default                                   |

¹ code freshness is covered by source-mtime reindexing, not calendar age.

`concept` and `entity` are **explicit-only** by design: inferring "this card is
about an entity" from prose is high false-positive (the same reason regex path
extraction was killed). The importer (Wave 1) and authors set them explicitly.

## Ranking weights

Compound weight = `evidence_weight × source_weight × drift_penalty`
(`bin/assemble_context.py:compound_weight`). Higher = ranked higher in recall.

```
EVIDENCE_WEIGHTS = { validated: 1.0, observed: 0.7, hypothesis: 0.4 }
SOURCE_WEIGHTS   = { user-explicit: 1.0, agent-extracted: 0.5, system-generated: 0.3 }
DRIFT_PENALTIES  = { broken_wikilink: 0.8, age_stale: 0.5, confidence_escalation: 0.3 }
```

`source: agent-extracted = 0.5` is the **self-referential discount** — the
system trusts what the agent wrote itself half as much as what the user said.
The Obsidian vault export uses a richer curation scale (foundational tier,
user-implicit source); that is deliberate and must not be folded into the
3-tier runtime constants.

## Lifecycle

- **status** — `current` (default) → `superseded` / `deprecated` / `obsolete` /
  `archived` / `resolved` / `fixed`. Inactive statuses are de-ranked, not
  deleted. Inferred from `superseded_by:` or name/description terms.
- **drift** (`bin/drift_check.py`) — three signals penalize stale cards:
  `broken_wikilink` (a `[[link]]` whose target left the index), `age_stale`
  (`last_verified` older than the kind/type threshold, and `evidence != hypothesis`),
  `confidence_escalation` (3+ agent-extracted updates, 0 user-explicit).
- **compounding** — a re-touched topic appends to the card's `## History`
  section rather than creating a duplicate. `last_verified` is _not_ bumped on a
  compound (it tracks human/explicit verification, not churn).

## Write paths

Four ways a card is created or updated. All converge on this schema.

1. **Author (agent) Write** — the agent writes a `.md` directly per the harness
   memory instructions. Used for durable facts/feedback/profile.
2. **Stop-hook auto-capture** — `hooks/session-signals.sh` extracts
   `Decision:/Rule:/Worked:/Failed:/Knowledge:` lines from the session, then
   `bin/compound.py` does search-before-write: append to a matching card's
   `## History`, else a new `signals/<date>.md`. Source = `agent-extracted`.
3. **Promotion** — `bin/remember.py "<title>"` files a full synthesized answer
   as one typed page (default `card_kind: synthesis`), with dedup: a re-promote
   appends a `## Update <date>` section instead of duplicating, and new pages get
   `## Related` wikilinks to neighbours.
4. **Importer** _(Wave 1, planned)_ — an external source (file/url/pdf/video) →
   extraction cards → this schema, with `source: imported` (a low-trust tier; its
   ranking weight is added to `SOURCE_WEIGHTS` when the importer lands) and a
   provenance span.

Every non-author write also appends a line to the greppable op-log
(`<memory-system>/log.md`) via `bin/oplog.py`:

```
grep '^## \[' log.md     # the whole timeline
grep -A3 promote log.md  # every promote with its detail
```
