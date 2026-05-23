---
type: rule
title: "Explain multi-vector topics one block at a time"
aliases: ["explain-multi-vector-topics-block-at-a-time"]
tags: ["rule"]
---

# Explain multi-vector topics one block at a time

> User wants technical explanations with 3+ independent vectors broken into numbered blocks with checkpoint questions between, not dumped together.

**Why:** A session dumped 8 vectors at once. User responded "I didn't get any of that... too many vectors — we need to discuss them one at a time or in blocks, in detail, so I understand". User profile: low Deliberative + low Adaptability — large simultaneous info loads overwhelm decision-making, especially when each vector branches into sub-decisions.

## Details

When a discussion has 3+ independent vectors (e.g. cleanliness + input coverage + text formula + classification path + scopes + schema bump + tracking + smoke gate + model swap), explain ONE block at a time. After each block, end with one checkpoint question — "clear, or expand here?" — wait for confirmation before next block.

**How to apply:**

- For any discussion with 3+ independent vectors: propose a numbered roadmap (4-7 blocks) FIRST, then deliver block 1 only.
- Each block: plain language + concrete example, not abstract terminology. Avoid nested matrices/tables when prose works.
- End each block with ONE checkpoint question. Do not move to block 2 until user confirms.
- Compatible with [[smoke-test-incrementally]] — same incremental philosophy applied to explanations.
- Counter-pattern: "executive summary with 6 sections, each having 4 bullets" = vector dump even if formatted nicely. Format ≠ pacing.

Related: [[formatting-readability]], [[brainstorm-leading-questions]].

_Confidence: high · Source: my-project_
