#!/usr/bin/env python3
"""FR-6 — semantic-door probe under the mlx venv (the M3 hook's blind spot).

The M3 hook re-execs itself under an SDK-capable python with no MLX
(`m3_hook._reexec_under_sdk_python`), so the in-process door
(`Index.neighbors(probe_text=...)` embeds the probe) fails to import the
embedder and returns [] on a store that HAS vectors — the mechanism behind the
live dup pair («какие треки сейчас открыты» / «…остались открытыми») filing as
two cards. This tiny CLI runs the SAME door under the mlx venv, so the hook
process gets real neighbors through a subprocess seam:

    argv[1] = index.db path
    argv[2] = JSON list of exclude paths (optional)
    stdin   = probe text
    stdout  = JSON list of neighbor hits

No new dedup logic, no thresholds — the existing door, made able to see.
Re-exec guard reused from embed.py (one fact, one place); embed.py's top-level
imports are stdlib-only, so importing it here is safe under the SDK python.
"""
import json
import os
import sys

_BIN = os.path.dirname(os.path.abspath(__file__))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)


def main():
    if len(sys.argv) < 2 or not os.path.isfile(sys.argv[1]):
        print("[]")
        return 0
    exclude = set()
    if len(sys.argv) > 2:
        try:
            exclude = set(json.loads(sys.argv[2]))
        except Exception:
            exclude = set()
    probe = sys.stdin.read()
    if not probe.strip():
        print("[]")
        return 0
    import m1_contradiction
    hits = m1_contradiction.neighbors_via_door(sys.argv[1], probe,
                                               exclude_paths=exclude)
    print(json.dumps(hits, ensure_ascii=False, default=float))
    return 0


if __name__ == "__main__":
    import embed
    embed._reexec_under_mlx_venv(target=os.path.abspath(__file__))
    sys.exit(main())
