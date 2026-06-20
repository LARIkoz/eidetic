# Install Eidetic with an agent

Give a coding agent (Claude Code, Codex, Cursor, …) this repo link and these steps.
The goal: a working install where the user has knowingly picked the **three models that
define the system** — the embedder, the cross-lingual translation backend (if any), and
the card-extraction model — ending with a green `doctor.sh`.

## Steps for the agent

1. **Clone & enter**

   ```bash
   git clone https://github.com/LARIkoz/eidetic.git && cd eidetic
   ```

2. **Ask the user the three choices** (state each default; if the user says "defaults",
   skip to step 3 with no env — the installer applies them):

   - **Embedder** — `multilingual` (e5-large, ~100 languages incl. Russian, ~2.2 GB)
     **[default]**, or `english` (bge-small-en, 384d, ~130 MB, ~5× faster, English-only).
   - **Query translation** (cross-lingual recall, opt-in) — `off` **[default]**, `auto`,
     `apple` (macOS 26+ on-device NMT), or `opusmt` (portable Opus-MT/CTranslate2).
   - **Card-extraction model** (the LLM that writes session-end memories) — `sonnet`
     (quality) **[default]**, or `haiku` (cheaper).

3. **Run the installer non-interactively** with the choices as env vars (omit any the
   user left at default):

   ```bash
   EIDETIC_NONINTERACTIVE=1 \
   EIDETIC_EMBED_PROFILE=multilingual \
   EIDETIC_QUERY_TRANSLATE=off \
   EIDETIC_SIGNAL_MODEL=sonnet \
   bash install.sh
   ```

   `EIDETIC_NONINTERACTIVE=1` makes the install skip prompts and take exactly these
   values (the same path CI and piped `curl | bash` installs use). The three choices are
   written to `~/.claude/memory-system/{.embed_profile,.translate_backend,.signal_model}`.

4. **If the user chose `apple` (or `auto` on macOS):** the Russian language pack is a
   one-time **GUI-only** download that cannot be scripted — the installer prints this, and
   you must relay it:

   > System Settings → General → Translation Languages → add **Russian** (~tens of MB).

   Until it's installed, `apple`/`auto` fail open to opusmt/cli/native — search still works.

5. **Verify** — run the doctor and report the verdict to the user:

   ```bash
   bash ~/.claude/memory-system/bin/doctor.sh
   ```

   A healthy install ends `VERDICT: ✅ healthy`. The doctor's **functional canary**
   (`embed → vector → search`) proves the chosen embedder actually embeds and ranks; the
   **Models — who does what** section shows all three choices; the **Apple translation
   pack** line confirms the language pack when `apple` is in play.

## Notes

- **One command also works without an agent:** `git clone … && cd eidetic && bash install.sh`
  prompts interactively on a TTY (enter = default for each of the three).
- **Optional semantic search** needs fastembed: `python3 -m pip install --user fastembed`.
  Without it, Eidetic runs FTS keyword search only (the canary skips cleanly).
- **Change a choice later:** write the new value to the matching config file
  (`.embed_profile` / `.translate_backend` / `.signal_model`); for the embedder, then run
  `bin/index.sh --full` to rebuild vectors under the new model.
