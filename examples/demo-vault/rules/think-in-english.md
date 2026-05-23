---
type: rule
title: Think in English always
aliases: ["think-in-english"]
tags: ["rule"]
---

# Think in English always

> Internal reasoning, plans, todos, and all artifacts must be in English. Only user-facing messages are in the user's language.

**Why:** Despite an existing Language Policy ("Think/search/reason in English"), internal thinking blocks had mixed languages. This leaks into mixed-language variable names, comments, section headers in created artifacts, and eventually mixed-language skills. User caught three skills still ~15-45% mixed during one session — a direct consequence of thinking in the wrong language and writing "translation" into artifacts instead of drafting in English.

## Details

Internal reasoning = English. Every thought, plan, todo item, scratchpad, comment, commit, doc, skill, memory entry — English.

Only **user-facing chat messages** are in the user's preferred language.

**How to apply:**

1. All `<thinking>` blocks: English only.
2. TodoWrite entries: English.
3. Commit messages, file paths, variable names, comments, docstrings, memory files, skill contents: English.
4. User's language only in the final visible chat message.
5. When reviewing a non-English user message: read/understand in source, but **rephrase the task mentally in English** before planning. Do not carry source language into plan/todo/code.
6. If drafting a long response in chat: write the draft in English mentally, then translate at final output time.

**Anti-pattern caught:** writing a skill with 30% non-English then having to translate it back. Write in English from the first keystroke — no "I'll translate later".

_Confidence: high · Source: my-project_
