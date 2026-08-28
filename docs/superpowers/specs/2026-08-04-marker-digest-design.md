# Marker Digest — Design

**Date:** 2026-08-04
**Status:** Approved (approach C). Built unattended at Sam's request; spec is here for review.

## Problem

The Stream Deck marker rig folds `[MARKER] <type>` lines into the merged stereo
transcript (via `casting_call.markers`). Those button presses are the cleanest,
most deliberate signal in a call — Sam physically pressed a key to say "this is
an Action / Question / Video moment." Nothing consumes them. `process-transcript`
reads the *spoken* asides on `[You]` lines and never looks at the `[MARKER]`
channel.

## Goal

From one merged transcript, produce a self-contained HTML **digest**: a short
call summary on top, then one collapsible section per marker type that appears,
each listing every press with the ~30s of conversation around it, a one-line
summary of the moment, a click-to-audio seek, and — for `video` markers — a
screenshot pulled from the recording at that instant.

## Approach (C): tested Python module + model-driven skill

Deterministic work lives in a tested module; the model does only language and
layout. This matches how casting-call is already built (`markers.py`,
`transcript.py`, each covered by pytest).

### Component 1 — `casting_call/digest.py` (deterministic, tested)

- Parse the transcript with `transcript.parse_transcript`.
- Split into speech lines and `[MARKER]` entries (label `MARKER`, from
  `markers.MARK_LABEL`); each marker's type is the line text (`flag`, `video`, …).
- For each marker, gather speech lines within ±`window` seconds (default 30),
  excluding other marker lines, sorted by time.
- Resolve sibling artifacts by the transcript's stem: `<name>.mp3` (audio),
  `<name>.mp4` then `<name>.mkv` (video). Search the transcript's dir and its
  parent (handles both `<name>/transcripts/<name>.txt` and flat layouts).
- For `video` markers only, ffmpeg-seek a single frame at the press time, scale
  to ~960px wide, JPEG, base64 into a `data:` URI.
- Emit one JSON blob to stdout: name, resolved audio/video paths, per-type
  `groups` in canonical `MARKER_TYPES` order (each with its markers in time
  order), total and per-type counts.

Output is grouped and ordered so the skill renders straight through with no
sorting logic of its own.

### Component 2 — `marker-digest` skill (model-driven)

`common/marker-digest/` (symlinked like `process-transcript`):

1. Resolve the handed-off transcript path(s); skip `-left`/`-right` variants and
   any transcript that already has a `<name>-digest.html` (unless forced).
2. Run `python3 -m casting_call.digest <txt>` → JSON.
3. If total markers == 0, print a notice and write nothing (old pre-rig
   transcripts have none — must be quiet, not an error).
4. Write the 2-4 sentence call summary (reads the full transcript).
5. Write a one-line summary per marker moment (reads its context window).
6. Render `<name>-digest.html` from the skill's `template.html`: summary, count
   row, one `<details>` per group, each moment = timestamp button (seeks a shared
   `<audio>`), one-liner, the ~30s quotes, and the embedded frame for `video`.
7. Write it next to the transcript; open in **Chrome** (reuse the
   `process-transcript` "skip if already in a tab" helper).

## Data flow

`transcript.txt` → `digest.py` (parse · window · ffmpeg frames) → JSON →
skill/model (summaries + render) → `<name>-digest.html` → open in Chrome.

## HTML digest

Top: call title + date, the summary, a compact count row
(`3 action · 5 question · 2 video`). Then one `<details>` per marker type present
(canonical order). A moment = timestamp button + AI one-liner + `[You]`/`[Caller]`
quotes + (video) embedded frame. Screenshots are base64 (self-contained); the
mp3 is referenced by `file://` path through one shared `<audio>` element that the
timestamp buttons seek. Inline CSS/JS, no external assets. Clean minimal styling
with `prefers-color-scheme`, not the print-ready authored-doc template (this is a
derived artifact, like `process-transcript`'s markdown).

## Error handling

- **No `[MARKER]` lines:** notice, write nothing.
- **No mp3:** timestamps render as plain text, no seek button.
- **No video file / ffmpeg fails on a frame:** that moment shows "no frame
  available"; the run continues.
- **ffmpeg missing:** frame extraction returns `None` gracefully (warn on
  stderr) rather than aborting the whole digest — better for unattended runs.

## Testing

pytest in the existing `casting_call/tests/` style: marker/speech split, the
±30s window (inclusive boundaries, excluding marker lines), timestamp
formatting, canonical group ordering, counts, empty-transcript case, stem-based
artifact resolution (temp files), and frame extraction with `subprocess.run`
monkeypatched (assert the ffmpeg command + base64 wrapping; no real video).

## Scope (v1, YAGNI)

Single transcript → one HTML digest. **Not** in v1: batch fan-out over a
directory (a later aggregator will pull daily bulk transcripts + digests),
hand-swapping screenshots, reprocess-detection beyond "skip if the digest exists
unless forced."
