---
type: rule
title: Fail Loud Not Silent
aliases: ["fail-loud-not-silent"]
tags: ["rule"]
---

# Fail Loud Not Silent

When something goes wrong, raise an error immediately rather than returning a default value or logging a warning and continuing. Silent failures propagate through the system and surface hours later as mysterious data corruption. A loud crash at the point of failure is far cheaper to debug than a quiet one discovered downstream.

## Related

- [[budget-envelopes]]
- [[test-behaviour-not-mocks]]
- [[verify-before-declaring-blockers]]
- [[back-up-before-destructive-ops]]
- [[pin-dependency-versions]]
