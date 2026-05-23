---
type: rule
title: Handoff requires 4-pass verification
aliases: ["handoff-requires-4-pass-verification"]
tags: ["rule"]
---

# Handoff requires 4-pass verification

> Single-pass handoff misses structural issues; 4-pass framework (language/links/orphans/inputs) catches them systematically.

**Why:** Different failure modes surface under different lenses:

- Linguistic lens → mixed-language files
- Structural lens → broken links
- Inventory lens → orphan scripts
- Input lens → missing data references
- Second-order lens → unvalidated assumptions

**How to apply:** After writing handoff docs, run this 4-pass verification before claiming done.

## Details

Writing a handoff doc and calling it done is **insufficient**. Every handoff in one observation window went through 8 iterations to reach truly complete state. Each pass found different classes of issues that prior passes couldn't catch.

### Pass 1: Language audit

```bash
grep -rE '[а-яА-Я]' --include='*.md' handoff-folder/ | grep -v 'RU:'
```

Non-trace Cyrillic = must translate. User quotes acceptable with `RU:` trace label.

### Pass 2: Dead links

```bash
for f in handoff-folder/*.md; do
  grep -oE '\]\([^)]+\.md\)' "$f" | while read raw; do
    link=$(echo "$raw" | sed 's/](//;s/)//')
    "$link" == http* && continue
    docdir=$(dirname "$f")
    [ ! -f "$docdir/$link" ] && echo "DEAD: $f → $link"
  done
done
```

Relative paths with `../` when crossing folders — never bare folder names.

### Pass 3: Orphan scripts

```bash
for script in /tmp/pipeline/*.py /tmp/*.py; do
  hits=$(grep -rl "$(basename $script)" handoff-folder/ 2>/dev/null | wc -l)
  $hits -eq 0 && echo "ORPHAN: $script"
done
```

Every script either documented in handoff OR moved to `legacy/` archive.

### Pass 4: Loader/script input validity

```bash
for loader in loaders/*.py; do
  grep -oE '[a-z_]+\.tsv' "$loader" | sort -u | while read tsv; do
    [ ! -f "/tmp/parse_out/$tsv" ] && echo "$loader → $tsv MISSING"
  done
done
```

### Additional mandatory checks

- `/tmp` rescue to handoff folder (manifest + small files only)
- Persistent backup refresh (rsync --update --delete)
- `BOOTSTRAP_RESTORE.sh` for reboot safety if using /tmp
- DB backup (if shared DB)
- MEMORY.md pointer update

### Separate HOLES vs BLIND_SPOTS passes

**Holes** = known-unknowns. Ask: "what's documented as incomplete?" Lists gaps you know exist.

**Blind spots** = unknown-unknowns. Ask: "what assumptions am I making that aren't validated?" Requires adversarial thinking.

Two separate docs. First is easy, second is hard. Both needed.

### Debug progression metric

8 iterations → 37 issues surfaced. **None would be caught without explicit verification pass.**

Codify as `verify_handoff.sh` for repeatable verification.

_Confidence: high · Source: my-project_
