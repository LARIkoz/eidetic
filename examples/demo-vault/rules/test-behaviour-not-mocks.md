---
type: rule
title: Test Behaviour Not Mocks
aliases: ["test-behaviour-not-mocks"]
tags: ["rule"]
---

# Test Behaviour Not Mocks

Tests should verify observable outcomes, not internal implementation details. A test that asserts a mock was called three times with specific arguments breaks every time the code is refactored, even if the behaviour stays correct. Prefer integration-style tests against real (or in-memory) databases and filesystems where practical.

## Related

- [[plant-care-reminder]]
- [[write-the-test-first]]
- [[fail-loud-not-silent]]
- [[sqlite-wal-mode]]
- [[verify-before-declaring-blockers]]
