# Contributing to Eidetic

Thanks for your interest! Contributions welcome.

## Quick Start

1. Fork the repo
2. `bash install.sh` to set up locally
3. Make your changes
4. Test: `~/.claude/memory-system/bin/health.sh`
5. Submit a PR

## What We Need Help With

- **Testing** — run it for a week, report edge cases
- **Performance** — profile at 1000+ files, find bottlenecks
- **Search quality** — queries that return wrong results or miss relevant files
- **New features** — see [Roadmap](#roadmap) in README

## Guidelines

- Zero external dependencies. bash + python3 stdlib + sqlite3 only.
- All file writes must be atomic (tempfile + os.replace).
- Hooks must fail gracefully (exit 0, no crash, no data loss).
- Agent-extracted content always gets `source: agent-extracted` (0.5x weight).

## Reporting Issues

Include:

- macOS/Linux version
- Claude Code version
- Output of `~/.claude/memory-system/bin/health.sh`
- What you expected vs what happened
