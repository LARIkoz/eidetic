---
type: rule
title: Pin Dependency Versions
aliases: ["pin-dependency-versions"]
tags: ["rule"]
---

# Pin Dependency Versions

Lock every dependency to an exact version in your manifest file. Floating ranges like `>=1.2` or `^3.0` invite breakage from upstream changes you did not review. When upgrading, read the changelog and check transitive dependencies before bumping the pin. Reproducible builds start with reproducible inputs.

## Related

- [[trail-run-tracker]]
- [[plant-care-reminder]]
- [[fail-loud-not-silent]]
- [[back-up-before-destructive-ops]]
- [[one-source-of-truth]]
