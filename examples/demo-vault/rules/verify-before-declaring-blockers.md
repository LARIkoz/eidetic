---
type: rule
title: Verify Before Declaring Blockers
aliases: ["verify-before-declaring-blockers"]
tags: ["rule"]
---

# Verify Before Declaring Blockers

Before labelling something a blocker, confirm the problem actually exists in the current state of the code. Stale assumptions, outdated branch state, and cached build artefacts cause most false blockers. Run the failing command yourself, check git status, and read the latest version of the file before escalating.

## Related

- [[test-behaviour-not-mocks]]
- [[review-your-own-diff]]
- [[fail-loud-not-silent]]
- [[back-up-before-destructive-ops]]
- [[indie-game-devlog]]
