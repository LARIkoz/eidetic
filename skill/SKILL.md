---
context: fork
agent: general-purpose
---

# Memory Recall

Search the FTS5 memory index for past decisions, rules, feedback, project context, and knowledge.

## Usage

Run the search command with a natural language query:

```bash
~/.claude/memory-system/bin/search.sh "<query>" --limit 5
```

### Options

- `--limit N` — number of results (default 10)
- `--type feedback|project|user|reference|code` — filter by memory type
- `--json` — legacy JSON list output for compatibility
- `--json-object` — structured agent output with `no_confident_results`

### Examples

```bash
# Find feedback rules
~/.claude/memory-system/bin/search.sh "feedback rules" --type feedback --limit 10

# Find a past decision
~/.claude/memory-system/bin/search.sh "deployment strategy decision"

# Find project context
~/.claude/memory-system/bin/search.sh "gap pipeline phase" --type project
```

## What to search for

- Decisions and rationale: "what did we decide about X"
- Behavioral rules: "rules for Y", "feedback about Z"
- Project state: "current status of project"
- Technical knowledge: "how does X work", "where is Y configured"
- Cross-project patterns: search without --type to find across all projects

## Output format

Each result shows:

- Score (compound: FTS5 rank × evidence × source × freshness)
- Confidence and confidence reason
- Card kind, lifecycle status, and supersession fields
- Drift findings when a memory is stale or otherwise suspect
- File path
- Memory name and type
- Section heading
- 200-char snippet

Results are ranked by compound score. Higher = more relevant + more trustworthy.

For agent consumers, prefer `--json-object`. If `no_confident_results` is
true, treat rows as weak candidates to inspect, not as usable memory.

## Important

- If a result has `contradicts:` in frontmatter — read BOTH sides before acting
- `agent-extracted` source = 0.5x weight (self-referential discount)
- Results older than 30 days have reduced freshness weight
- Superseded/deprecated/archived memories are downranked and should be verified before use
