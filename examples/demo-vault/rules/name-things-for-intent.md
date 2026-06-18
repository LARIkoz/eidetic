---
type: rule
title: Name Things for Intent
aliases: ["name-things-for-intent"]
tags: ["rule"]
---

# Name Things for Intent

Variable, function, and file names should describe what something is for, not how it works internally. A name like `retry_on_network_error` communicates intent; `loop_3_times` communicates mechanism. Intent-based names survive refactors because the purpose outlasts the implementation.

## Related

- [[indie-game-devlog]]
- [[keep-functions-small]]
- [[few-shot-beats-instructions]]
- [[one-source-of-truth]]
- [[prefer-composition-over-inheritance]]
