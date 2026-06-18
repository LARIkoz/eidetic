---
type: rule
title: Prefer Composition Over Inheritance
aliases: ["prefer-composition-over-inheritance"]
tags: ["rule"]
---

# Prefer Composition Over Inheritance

Build behaviour by combining small, focused components rather than extending deep class hierarchies. Inheritance creates tight coupling that makes changes ripple unpredictably through the tree. Composition keeps each piece testable in isolation and lets you swap implementations without rewriting parent contracts.

## Related

- [[keep-functions-small]]
- [[name-things-for-intent]]
- [[flashcard-tutor]]
- [[test-behaviour-not-mocks]]
- [[write-the-test-first]]
