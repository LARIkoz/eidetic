#!/usr/bin/env python3
"""eidetic base — manage attachable topic knowledge-bases (PULL), separate from your
personal PUSH memory.

A base is its OWN folder/git-repo: `docs/` (ingested) + `notes/` (curated) +
`.eidetic-base.json` (manifest) + `db/` (gitignored index). It scans ONLY its corpus
(never `~/.claude`) and is attached to a project on demand over MCP — see
`docs/topic-bases.md`.

Subcommands:
  init <name> [--dir DIR]            scaffold <DIR>/<name>-base/ (+ register)
  index <name|path> [--incremental]  build the base index (default: full = FTS + vectors)
  add <name|path> (--file F | --text T) [--as note|doc] [--title T]
                                     curate-write one md into the base, then reindex
  attach <name|path> [--scope project|user] [--run]
                                     print (or run) the `claude mcp add …` line
  list                               list registered bases
  doctor <name|path>                 functional canary (embed→vector→search) vs the base
  refresh <name|path>                reindex (host-only: re-run the scrape recipe first)

Zero external deps — python3 stdlib only.
"""

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile

BIN = os.path.dirname(os.path.abspath(__file__))
EIDETIC_ROOT = os.path.dirname(BIN)
MCP_SERVER = os.path.join(EIDETIC_ROOT, "mcp_server.py")
INDEX_SH = os.path.join(BIN, "index.sh")
REGISTRY = os.path.expanduser(os.environ.get("EIDETIC_BASES_REGISTRY") or "~/.claude/eidetic-bases.json")
# bases live together under one root, kept OUT of any project tree (a base is a separate
# PULL repo, never your personal memory). Override per-machine with EIDETIC_BASES_DIR.
DEFAULT_BASES_DIR = "~/eidetic-bases"

MANIFEST = ".eidetic-base.json"
ADD_SIZE_THRESHOLD = 2000  # chars: smaller → a note card, larger → a doc page
# A base name becomes an MCP tool prefix (`<name>_search`) AND is printed into the
# `claude mcp add <name> …` line. Restrict it so a manifest can't inject shell or emit a
# protocol-invalid tool name: lowercase, start with a letter, only [a-z0-9_-], ≤40.
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")


def _require_valid_name(name):
    if not (isinstance(name, str) and NAME_RE.match(name)):
        sys.exit(f"error: invalid base name {name!r} — must match {NAME_RE.pattern} "
                 f"(lowercase, start with a letter, only a-z 0-9 _ - , ≤40 chars)")
    return name


# --- corpus language (for cross-lingual query translation). A base records its
# dominant language in a `.translate_lang` file at its root; search reads it to
# translate a foreign-language query INTO the corpus language. Auto-detected at
# index time (dominance-thresholded so a mostly-English corpus stays unset/English),
# or set explicitly via `init --lang`.
TRANSLATE_LANG_FILE = ".translate_lang"
_SCRIPT_BLOCKS = [
    ("ru", (0x0400, 0x052F)), ("ar", (0x0600, 0x06FF)),
    ("ja", (0x3040, 0x30FF)), ("ko", (0xAC00, 0xD7AF)), ("zh", (0x3400, 0x9FFF)),
]


def _detect_corpus_lang(base, threshold=0.5, sample=500):
    """The dominant non-Latin language across the indexed corpus, or None for a
    Latin-script (English/de/fr/…) corpus. A non-Latin script must exceed `threshold`
    of all alphabetic chars to win, so a few Cyrillic words in an English base stay
    None (English) — never mis-translate the majority language away."""
    import sqlite3
    db = os.path.join(base, "db", "index.db")
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute("SELECT COALESCE(content,'') FROM memory_chunks LIMIT ?",
                            (sample,)).fetchall()
        conn.close()
    except sqlite3.Error:
        return None
    latin = 0
    counts = {lang: 0 for lang, _ in _SCRIPT_BLOCKS}
    for (text,) in rows:
        for ch in text:
            o = ord(ch)
            if 0x41 <= o <= 0x7A:
                latin += 1
            else:
                for lang, (a, b) in _SCRIPT_BLOCKS:
                    if a <= o <= b:
                        counts[lang] += 1
                        break
    total = latin + sum(counts.values())
    if total == 0:
        return None
    lang, n = max(counts.items(), key=lambda kv: kv[1])
    return lang if n / total >= threshold else None


def _write_translate_lang(base, lang):
    if lang:
        with open(os.path.join(base, TRANSLATE_LANG_FILE), "w", encoding="utf-8") as f:
            f.write(lang.strip().lower() + "\n")


# --------------------------------------------------------------------------- registry
def _load_registry():
    try:
        with open(REGISTRY, encoding="utf-8") as f:
            r = json.load(f)
        return r if isinstance(r, dict) else {}
    except (OSError, ValueError):
        return {}


def _register(name, path):
    # serialize the read-modify-write under an exclusive lock + write through a UNIQUE
    # temp file → two parallel `base init` can't race on a shared tmp or lose each other.
    os.makedirs(os.path.dirname(REGISTRY), exist_ok=True)
    with open(REGISTRY + ".lock", "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            r = _load_registry()
            r[name] = os.path.abspath(path)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(REGISTRY), prefix=".reg-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(r, f, indent=2, ensure_ascii=False)
            os.replace(tmp, REGISTRY)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def resolve_base(arg):
    """A base path (dir containing .eidetic-base.json) or a registered name -> abs path."""
    cand = os.path.abspath(os.path.expanduser(arg))
    if os.path.exists(os.path.join(cand, MANIFEST)):
        return cand
    reg = _load_registry().get(arg)
    if reg and os.path.exists(os.path.join(reg, MANIFEST)):
        return reg
    sys.exit(f"error: no base named or at '{arg}' (no {MANIFEST} found). "
             f"`eidetic base list` shows registered bases.")


def read_manifest(base):
    with open(os.path.join(base, MANIFEST), encoding="utf-8") as f:
        return json.load(f)


def _slug(text, fallback="note"):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (s[:60] or fallback)


# ------------------------------------------------------------------------------- init
def cmd_init(args):
    name = _require_valid_name(args.name)
    # precedence: explicit --dir > EIDETIC_BASES_DIR env > neutral default bases-root.
    # NOT cwd — a base must not land loose inside whatever project you happen to be in.
    parent = os.path.abspath(os.path.expanduser(
        args.dir or os.environ.get("EIDETIC_BASES_DIR") or DEFAULT_BASES_DIR))
    os.makedirs(parent, exist_ok=True)
    base = os.path.join(parent, f"{name}-base")
    if os.path.exists(os.path.join(base, MANIFEST)):
        sys.exit(f"error: a base already exists at {base}")
    for sub in ("docs", "notes", "db"):
        os.makedirs(os.path.join(base, sub), exist_ok=True)
    manifest = {"name": name, "corpus_dirs": ["docs", "notes"], "db": "db/index.db"}
    with open(os.path.join(base, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    with open(os.path.join(base, ".gitignore"), "w", encoding="utf-8") as f:
        f.write("db/\n")          # index is a rebuilt artifact — keep the repo source-only
    with open(os.path.join(base, "docs", "HOME.md"), "w", encoding="utf-8") as f:
        f.write(f"# {name}\n\nTopic base. Add scraped docs under `docs/`, curated facts "
                f"under `notes/`. Cross-link with [[wikilinks]].\n")
    _register(name, base)
    if getattr(args, "lang", None):
        _write_translate_lang(base, args.lang)
        print(f"  corpus language: {args.lang.strip().lower()} (cross-lingual queries translate into it)")
    print(f"created base '{name}' at {base}")
    print(f"  next: add docs under {base}/docs/, then  eidetic base index {name}")
    print(f"  attach:  eidetic base attach {name} --scope project")
    return 0


# ------------------------------------------------------------------------------ index
def _run_index(base, full):
    env = dict(os.environ, EIDETIC_MEMORY_SYSTEM=base)
    mode = "--full" if full else "--incremental"
    return subprocess.run(["bash", INDEX_SH, mode], env=env).returncode


def cmd_index(args):
    base = resolve_base(args.name)
    # default = full (FTS + vectors) — a base is small, built once; --incremental = FTS only
    rc = _run_index(base, full=not args.incremental)
    if rc == 0:
        # auto-stamp the corpus language once (dominance-thresholded) so cross-lingual
        # query translation targets THIS base's language — unless already set/explicit.
        if not os.path.exists(os.path.join(base, TRANSLATE_LANG_FILE)):
            detected = _detect_corpus_lang(base)
            if detected:
                _write_translate_lang(base, detected)
                print(f"detected corpus language: {detected} → wrote {TRANSLATE_LANG_FILE} "
                      f"(cross-lingual queries now translate into {detected})")
        print(f"indexed base at {base}")
    return rc


# -------------------------------------------------------------------------------- add
def cmd_add(args):
    base = resolve_base(args.name)
    if args.file:
        with open(os.path.expanduser(args.file), encoding="utf-8") as f:
            body = f.read()
        default_title = os.path.splitext(os.path.basename(args.file))[0]
    else:
        body = args.text or ""
        default_title = (body.strip().splitlines() or ["note"])[0][:60]
    if not body.strip():
        sys.exit("error: nothing to add (empty --file/--text)")

    title = args.title or default_title
    kind = args.as_ or ("doc" if len(body) >= ADD_SIZE_THRESHOLD else "note")
    subdir, ctype = ("docs", "reference") if kind == "doc" else ("notes", "note")

    # reuse eidetic's frontmatter schema; tag provenance source:user (curated)
    has_fm = body.lstrip().startswith("---")
    fm = "" if has_fm else (
        f"---\nname: {title}\ndescription: {title}\ntype: {ctype}\n"
        f"metadata:\n  source: user\n---\n\n")
    dest = os.path.join(base, subdir, f"{_slug(title)}.md")
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(base, subdir, f"{_slug(title)}-{n}.md")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(fm + body.rstrip() + "\n")
    print(f"added {kind} -> {os.path.relpath(dest, base)}")
    return _run_index(base, full=False)  # incremental: FTS now; run `index` for vectors


# ----------------------------------------------------------------------------- attach
def cmd_attach(args):
    base = resolve_base(args.name)
    name = _require_valid_name(read_manifest(base).get("name") or os.path.basename(base))
    cmd = ["claude", "mcp", "add", name, "-s", args.scope,
           "-e", f"EIDETIC_MEMORY_SYSTEM={base}", "--", "python3", MCP_SERVER]
    line = " ".join(cmd)
    if args.run:
        print(f"$ {line}")
        return subprocess.run(cmd).returncode
    print(line)
    print(f"\n# tools exposed in the project: {name}_search / {name}_search_detail / "
          f"{name}_add\n# detach:  claude mcp remove {name}")
    return 0


# ------------------------------------------------------------------------------- list
def cmd_list(args):
    reg = _load_registry()
    if not reg:
        print("no registered bases. create one: eidetic base init <name>")
        return 0
    for name, path in sorted(reg.items()):
        live = os.path.exists(os.path.join(path, MANIFEST))
        print(f"  {name:<24} {path}{'' if live else '   (MISSING)'}")
    return 0


# ----------------------------------------------------------------------------- doctor
def cmd_doctor(args):
    base = resolve_base(args.name)
    db = os.path.join(base, "db", "index.db")
    vec = os.path.join(base, "db", "vectors.db")
    if not os.path.exists(db):
        sys.exit(f"error: base not indexed yet (no {db}). run: eidetic base index {args.name}")
    # don't report green on an EMPTY index — the canary's "skip" would otherwise hide a
    # base that was init'd but never indexed (or whose db was wiped).
    import sqlite3
    try:
        with sqlite3.connect(db) as c:
            n_chunks = c.execute("SELECT count(*) FROM memory_chunks").fetchone()[0]
    except sqlite3.Error:
        n_chunks = 0
    if n_chunks == 0:
        print(f"FAIL: base index is EMPTY (0 chunks) — add docs under {base}/docs/ then: "
              f"eidetic base index {args.name}")
        return 2
    sys.path.insert(0, BIN)
    import canary
    emb = canary.embed_canary(db, vec)
    print(f"chunks={n_chunks} · embed→vector→search: {emb['status']} — {emb.get('detail','')}")
    return 0 if emb["status"] in ("ok", "warn", "skip") else 1


# ---------------------------------------------------------------------------- refresh
def cmd_refresh(args):
    base = resolve_base(args.name)
    print("host-only: re-run your scrape recipe to update docs/ first (see "
          "docs/topic-bases.md), then this rebuilds the index.")
    return _run_index(base, full=True)


# ---------------------------------------------------------------- mlx interpreter route
# The mlx embed engine is installed ONLY in the py3.12 `eidetic-mlx` venv. base.py is
# invoked as `python3 base.py …` (system interpreter, no mlx), so the in-process embed
# canary in `doctor` would silently fall back to FTS-only (canary → "skip", reported
# green). When mlx is the selected engine, re-exec under the venv interpreter — the
# python-entrypoint equivalent of the PATH prepend in index/search/doctor/update.sh + the
# 2 session hooks (the 6 shell entrypoints; base.py doctor was the missed 7th). No-ops
# off-mlx or without the venv, so it is safe on every machine. Opt out with
# EIDETIC_NO_MLX_REEXEC=1. Reversible: delete this function and its call in __main__.
def _reexec_under_mlx_venv():
    if os.environ.get("EIDETIC_MLX_REEXEC") or os.environ.get("EIDETIC_NO_MLX_REEXEC"):
        return  # already re-exec'd (loop guard) or explicitly opted out
    engine = os.environ.get("EIDETIC_EMBED_ENGINE", "").strip()  # same order as embed.py
    if not engine:
        try:
            with open(os.path.join(EIDETIC_ROOT, ".embed_engine"), encoding="utf-8") as f:
                engine = f.read().strip()
        except OSError:
            return  # no engine file → default (fastembed/CPU); nothing to route
    if engine != "mlx":
        return
    venv_py = os.path.expanduser("~/.venvs/eidetic-mlx/bin/python3")
    if not os.path.exists(venv_py) or os.path.realpath(venv_py) == os.path.realpath(sys.executable):
        return  # venv absent (other machines) or already running the venv interpreter
    os.environ["EIDETIC_MLX_REEXEC"] = "1"
    os.execv(venv_py, [venv_py, os.path.abspath(__file__), *sys.argv[1:]])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="eidetic base", description="manage topic knowledge-bases")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("name")
    p.add_argument("--dir", help="parent dir for <name>-base/ (default: $EIDETIC_BASES_DIR or ~/eidetic-bases)")
    p.add_argument("--lang", help="corpus language code (e.g. ru) for cross-lingual query translation; auto-detected at index time if omitted")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("index"); p.add_argument("name"); p.add_argument("--incremental", action="store_true"); p.set_defaults(fn=cmd_index)
    p = sub.add_parser("add"); p.add_argument("name")
    p.add_argument("--file"); p.add_argument("--text"); p.add_argument("--title")
    p.add_argument("--as", dest="as_", choices=["note", "doc"]); p.set_defaults(fn=cmd_add)
    p = sub.add_parser("attach"); p.add_argument("name")
    p.add_argument("--scope", choices=["project", "user", "local"], default="project")
    p.add_argument("--run", action="store_true"); p.set_defaults(fn=cmd_attach)
    p = sub.add_parser("list"); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("doctor"); p.add_argument("name"); p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("refresh"); p.add_argument("name"); p.set_defaults(fn=cmd_refresh)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    _reexec_under_mlx_venv()
    sys.exit(main())
