#!/usr/bin/env python3
"""Eidetic query translation — pluggable cross-lingual backend (default OFF).

When enabled, a non-English query is translated to English so it gains English
lexical/semantic anchors. search_impl's async dual-query runs the native and the
translated query in parallel and fuses by best rank — so translation is an
enhancement (measured 5/8 -> 7/8 recall@3), never a regression: every backend is
FAIL-OPEN (returns None on any failure → the caller keeps the native result).

Backends:
  apple   Apple Translation NMT (macOS 26+, on-device, owner-preferred). Shells to
          a tiny Swift helper (apple_translate.swift) using the headless
          TranslationSession(installedSource:target:) API. Needs the language pair
          installed once via System Settings → Translation Languages.
  opusmt  Helsinki Opus-MT via CTranslate2 (portable, offline, Linux + macOS).
          Lazy `pip: ctranslate2 sentencepiece huggingface_hub` + a ~75MB INT8
          model. ru→en only in v1.
  cli     codex CLI (zero-install fallback; network + latency). Best-effort.
  off     no translation (default — search is byte-identical to no-feature).
  auto    prefer apple (macOS + pack installed) → opusmt (ct2 + model) → cli → None.

Selection: env EIDETIC_QUERY_TRANSLATE, else the `.translate_backend` file at the
memory-system root, else "off".

CLI (used by recall_lab + doctor + manual test):
    translate.py "<text>" [--to en] [--backend apple|opusmt|cli|auto] [--status]
"""

import os
import shutil
import subprocess
import sys

BIN = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BIN)
CACHE = os.path.expanduser(os.environ.get("EIDETIC_CACHE") or "~/.cache/eidetic")

VALID_BACKENDS = {"off", "auto", "apple", "opusmt", "cli"}

# --- opusmt model (community CTranslate2 conversion of Helsinki-NLP/opus-mt-ru-en).
# Pinned by revision for reproducibility + supply-chain safety (weights, not code).
OPUSMT_MODEL = os.environ.get("EIDETIC_OPUSMT_MODEL", "gaudi/opus-mt-ru-en-ctranslate2")
OPUSMT_REVISION = os.environ.get("EIDETIC_OPUSMT_REVISION",
                                 "d5c98dcc59a677e768e00ab6e29fbdea80a53729")

APPLE_SRC = os.path.join(BIN, "apple_translate.swift")
APPLE_BIN = os.path.join(CACHE, "apple_translate")


# --------------------------------------------------------------------------- config
def active_backend(_config_path=None):
    """Resolve the configured backend: env, else .translate_backend file, else off."""
    name = os.environ.get("EIDETIC_QUERY_TRANSLATE", "").strip()
    if not name:
        cfg = _config_path or os.path.join(ROOT, ".translate_backend")
        try:
            with open(cfg, encoding="utf-8") as f:
                name = f.read().strip()
        except OSError:
            name = ""
    return name if name in VALID_BACKENDS else "off"


# ------------------------------------------------------------------ language detection
# Dependency-free, portable. Cyrillic / CJK / kana / Hangul / Arabic present and the
# target is English ⇒ the query is cross-lingual ⇒ translate. (On macOS the apple
# helper additionally uses NLLanguageRecognizer for source detection.)
_SCRIPT_RANGES = (
    (0x0400, 0x052F),   # Cyrillic + supplement
    (0x0600, 0x06FF),   # Arabic
    (0x3040, 0x30FF),   # Hiragana + Katakana
    (0x3400, 0x9FFF),   # CJK
    (0xAC00, 0xD7AF),   # Hangul
)


def is_non_english(text):
    for ch in text or "":
        o = ord(ch)
        if any(a <= o <= b for a, b in _SCRIPT_RANGES):
            return True
    return False


def should_translate(query, target="en"):
    """Cheap gate: only translate a non-English query when the target is English."""
    return target == "en" and is_non_english(query)


# ------------------------------------------------------------------------ apple backend
def _ensure_apple_bin():
    """Compile the Swift helper to the cache on first use. macOS + swiftc only.
    Compiles to a per-pid temp path then os.replace()s it into place — an atomic
    rename so two concurrent searches (the owner fans out parallel sessions) can
    never write a half-built binary to the path we exec."""
    if os.path.exists(APPLE_BIN):
        return True
    if sys.platform != "darwin" or not shutil.which("swiftc") or not os.path.exists(APPLE_SRC):
        return False
    tmp = None
    try:
        os.makedirs(CACHE, exist_ok=True)
        tmp = f"{APPLE_BIN}.{os.getpid()}.tmp"
        subprocess.run(
            ["swiftc", "-parse-as-library", "-O", APPLE_SRC, "-o", tmp],
            check=True, capture_output=True, timeout=180,
        )
        os.replace(tmp, APPLE_BIN)
        return os.path.exists(APPLE_BIN)
    except Exception:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False


def apple_available(target="en", source="ru"):
    """True when the helper compiles AND the pair's pack is installed."""
    if not _ensure_apple_bin():
        return False
    try:
        r = subprocess.run([APPLE_BIN, "--status", "--from", source, "--to", target],
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0 and "installed" in r.stdout and "notInstalled" not in r.stdout
    except Exception:
        return False


def _apple_translate(query, target):
    if not _ensure_apple_bin():
        return None
    try:
        r = subprocess.run([APPLE_BIN, query, "--to", target],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return r.stdout.strip() or None
    except Exception:
        return None
    return None


# ----------------------------------------------------------------------- opusmt backend
_opusmt = None  # (translator, sp_src, sp_tgt) | False once load failed


def _opusmt_load():
    global _opusmt
    if _opusmt is not None:
        return _opusmt
    try:
        import ctranslate2
        import sentencepiece as spm
        from huggingface_hub import snapshot_download
        d = snapshot_download(OPUSMT_MODEL, revision=OPUSMT_REVISION)
        tr = ctranslate2.Translator(d, device="cpu", compute_type="int8")
        sp_s = spm.SentencePieceProcessor(os.path.join(d, "source.spm"))
        sp_t = spm.SentencePieceProcessor(os.path.join(d, "target.spm"))
        _opusmt = (tr, sp_s, sp_t)
    except Exception:
        _opusmt = False
    return _opusmt


def opusmt_available():
    """Light check — deps importable. The ~75MB model loads lazily on the first
    real translate (not here), so the doctor and auto-resolution stay cheap."""
    import importlib.util
    return all(importlib.util.find_spec(m) for m in
               ("ctranslate2", "sentencepiece", "huggingface_hub"))


def _opusmt_translate(query, target):
    if target != "en":          # v1 ships the ru→en model only
        return None
    m = _opusmt_load()
    if not m:
        return None
    tr, sp_s, sp_t = m
    try:
        # Opus-MT/Marian REQUIRES the source to end with </s> (CTranslate2 does not
        # add EOS) — without it the decoder never emits EOS and loops. The anti-repeat
        # guards keep a degenerate hypothesis from leaking a token-spam "translation".
        toks = sp_s.encode(query, out_type=str) + ["</s>"]
        res = tr.translate_batch([toks], beam_size=4, max_decoding_length=128,
                                 no_repeat_ngram_size=3, repetition_penalty=1.1)
        return sp_t.decode(res[0].hypotheses[0]).strip() or None
    except Exception:
        return None


# -------------------------------------------------------------------------- cli backend
def cli_available():
    return shutil.which("codex") is not None


def _cli_translate(query, target):
    """Zero-install fallback via codex (the signal-extraction route — avoids a
    `claude --print` Anthropic-quota kickout while interactive Opus is live).
    Best-effort: rejects agent-noise output and fails open."""
    codex = shutil.which("codex")
    if not codex:
        return None
    prompt = (f"Translate the following text to {target}. "
              f"Output ONLY the translation on a single line, no quotes, no commentary:\n{query}")
    try:
        r = subprocess.run([codex, "exec", "--skip-git-repo-check", prompt],
                           capture_output=True, text=True, timeout=45)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    if not lines:
        return None
    out = lines[-1]
    # Sanity: a translation is not 5x the source nor multi-paragraph agent chatter.
    if len(out) > max(80, 5 * len(query)):
        return None
    return out or None


# --------------------------------------------------------------------------- dispatch
_BACKENDS = {
    "apple": _apple_translate,
    "opusmt": _opusmt_translate,
    "cli": _cli_translate,
}


def resolve_backend(name=None):
    """Map 'auto'/None to a concrete available backend (or None if none available)."""
    name = name or active_backend()
    if name in ("off", None):
        return None
    if name == "auto":
        if apple_available():
            return "apple"
        if opusmt_available():
            return "opusmt"
        if cli_available():
            return "cli"
        return None
    return name if name in _BACKENDS else None


# query→translation cache keyed on the RESOLVED backend (so an "auto"/None key
# can't go stale across an availability/config change) and storing SUCCESSES ONLY
# (a transient backend failure must not poison that query for the process life).
_translate_cache = {}


def clear_cache():
    _translate_cache.clear()


def translate(query, target="en", backend=None):
    """Translate `query` to `target`. Returns the translation, or None when the
    backend is off/unavailable/failed (caller falls open to the native query)."""
    if not query or not query.strip():
        return None
    resolved = resolve_backend(backend)
    if not resolved:
        return None
    key = (query, target, resolved)
    if key in _translate_cache:
        return _translate_cache[key]
    fn = _BACKENDS.get(resolved)
    if not fn:
        return None
    out = fn(query, target)
    if out:                       # cache only successes — a transient failure retries
        _translate_cache[key] = out
    return out


def backend_status(source="ru"):
    """For the doctor: the configured backend + per-backend availability. `source` is
    the query language whose Apple pack is probed (source->en) — the doctor passes the
    corpus/configured language so the pack check isn't hardcoded to Russian."""
    configured = active_backend()
    resolved = resolve_backend(configured) if configured != "off" else None
    return {
        "configured": configured,
        "resolved": resolved,
        "apple": apple_available(source=source),
        "apple_source": source,
        "opusmt": opusmt_available(),
        "cli": cli_available(),
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    backend = None
    target = "en"
    status_only = False
    parts = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--backend" and i + 1 < len(argv):
            backend = argv[i + 1]; i += 2
        elif a == "--to" and i + 1 < len(argv):
            target = argv[i + 1]; i += 2
        elif a == "--status":
            status_only = True; i += 1
        else:
            parts.append(a); i += 1

    if status_only:
        st = backend_status()
        print(f"configured={st['configured']} resolved={st['resolved']} "
              f"apple={st['apple']} opusmt={st['opusmt']} cli={st['cli']}")
        return 0

    text = " ".join(parts).strip()
    if not text:
        text = sys.stdin.read().strip()
    if not text:
        print("usage: translate.py <text> [--to en] [--backend apple|opusmt|cli|auto] [--status]",
              file=sys.stderr)
        return 2
    # An explicit --backend overrides the off default for ad-hoc testing.
    out = translate(text, target, backend or "auto")
    if out is None:
        print("(no translation — backend off/unavailable)", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
