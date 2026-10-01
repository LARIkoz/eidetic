#!/usr/bin/env python3
"""Run the session-signal extraction prompt on shimnachi/local.

Owner decision D9 (2026-09-24, confirmed 2026-10-01): the owner's conversations are
processed locally only. session-signals.sh pipes its prompt here when
EIDETIC_SIGNAL_LOCAL=shimnachi. stdin: the transcript prompt; EIDETIC_SIGNAL_SYSTEM:
the extractor frame; stdout: the model's text. Exit 1 on any failure, never a
fallback to another route.
"""
import os
import sys

BIN_DIR = os.path.dirname(os.path.realpath(__file__))
if BIN_DIR not in sys.path:
    sys.path.insert(0, BIN_DIR)


def _reexec_under_sdk_python() -> None:
    """The hook puts the isolated eidetic-mlx venv first on PATH, and that venv cannot
    import the shared SDK (no `dotenv`). Re-exec under a python3 that can, as
    m3_hook._reexec_under_sdk_python does for the miner. Runs before stdin is read."""
    if os.environ.get("EIDETIC_SIGNAL_PY_REEXEC"):
        return
    try:
        import dotenv  # noqa: F401  proxy for "this interpreter can load the SDK"
        return
    except ImportError:
        pass
    import subprocess
    for cand in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"):
        if not os.path.exists(cand) or os.path.realpath(cand) == os.path.realpath(sys.executable):
            continue
        try:
            subprocess.run([cand, "-c", "import dotenv"], check=True, timeout=15,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            continue
        os.environ["EIDETIC_SIGNAL_PY_REEXEC"] = "1"
        os.execv(cand, [cand, os.path.realpath(__file__), *sys.argv[1:]])


def main() -> int:
    _reexec_under_sdk_python()
    prompt = sys.stdin.read()
    if not prompt.strip():
        return 1
    root = os.environ.get("EIDETIC_SHARED_ROOT") or os.path.join(
        os.path.expanduser("~"), "Documents/cursore")
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        import m3_recall_miner
        m3_recall_miner._ensure_shimnachi_class_token(root)  # noqa: SLF001
        from shared_api_cache import get_sdk
        res = get_sdk().chat_for_route(
            provider="shimnachi", model="local", task="memory_mining", volume="bounded",
            allow_same_family_failover=False,
            system=os.environ.get("EIDETIC_SIGNAL_SYSTEM", ""), user=prompt,
            max_tokens=1500, temperature=0.0,
            timeout=int(os.environ.get("EIDETIC_SIGNAL_LOCAL_TIMEOUT", "120")))
    except Exception as exc:  # SDK absent or route down: report, no fallback
        print(f"local_signal_extract: {exc!r}"[:300], file=sys.stderr)
        return 1
    shape = res.get("response_shape") or {}
    text = (res.get("content") or "").strip()
    if not shape.get("ok") or not text:
        print(f"local_signal_extract: not ok ({shape.get('provider_error_class')})", file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
