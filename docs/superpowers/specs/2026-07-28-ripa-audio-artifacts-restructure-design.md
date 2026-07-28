# ripa Output Restructure: Audio Artifacts + Per-Recording Folder

**Project:** casting-call (`~/Developer/casting-call`)
**Date:** 2026-07-28
**Status:** Design approved, not yet built.
**Owner:** Sam Gutentag
**Branch:** feat/multitrack-markers

## Problem

`ripa` currently produces one thing per recording: a merged transcript at
`<folder>/transcripts/<name>.txt`. The per-track audio it extracts is transient (16 kHz mono WAVs
in `/tmp`, deleted at the end), so there is no way to listen back to a call, to a single track, or
to hand someone the audio. Everything useful is thrown away except the text.

The redesign keeps all existing transcript processing and adds audio deliverables, reorganized so
each recording owns a folder: the combined audio and merged transcript at the top, the per-track
pieces tucked into a `parts/` subdirectory.

## Scope

`ripa` (`bin/extract_audio_stereo.sh`) only. The batch script `ript`
(`transcribe_batch_stereo.sh`) and the docs keep the old `transcripts/` layout for now and go
temporarily inconsistent. A later pass can bring them in line.

## Output layout

Per recording `<name>.mkv` (or `.mp4`) in the input folder:

```
<folder>/<name>/
  <name>.mp3          combined: all present tracks mixed, loudnorm'd, mono
  <name>.txt          merged, cleaned, markers folded, relabeled
  parts/
    you.mp3  caller.mp3  markers.mp3     per-track, source-rate; you/caller loudnorm'd
    you.txt  caller.txt  markers.txt     per-track raw transcripts, timestamped
```

`markers.mp3` / `markers.txt` are written only when Track 3 (`0:a:2`) exists.

## Locked decisions

1. **Combined `<name>.mp3` mixes all present tracks** (you + caller + markers when present), mono.
   Not the you=L / caller=R split. The Track 3 marker cues are audible in the mix.
2. **Deliverable MP3s keep the source sample rate**, not the 16 kHz whisper downsample, so they are
   real listen-back quality. Encoded with `libmp3lame` (already in the brew ffmpeg build, no new
   dependency).
3. **`parts/` transcripts are the raw per-track output**, one file per track, timestamped. The
   top-level `<name>.txt` is the fully processed one (weave + clean + markers + relabel). Parts are
   deliberately pre-cleanup so they show exactly what each track transcribed.
4. **you/caller MP3s get the existing `loudnorm`**; markers MP3 is untouched, matching how the
   marker track is handled everywhere else.
5. **The `ripa` alias and script name stay.** The header comment already flags an eventual rename
   to `transcribe_multitrack.sh`; out of scope here.

## Pipeline

### Phase 1: single demux pass (one `ffmpeg -i`)

Builds on the single-demux refactor already landed on this branch. One `ffmpeg -i "$input_file"`
reads the container once and emits, per mapped track, **two** outputs:

- a transient 16 kHz mono WAV (for whisper), in a temp workdir
- a source-rate MP3 (the deliverable), into `parts/`

In the same invocation, a `filter_complex` mixes all present tracks into the combined MP3:
per-track `loudnorm`, then `amix`, then one `loudnorm` on the mixed result so the combined file
sits at a sane level. Output: `<name>/<name>.mp3`.

The argument list is built conditionally so the Track 3 outputs and the third `amix` input only
appear when `has_stream ... a:2` is true. Single-track and two-track recordings behave as they do
today.

### Phase 2: transcribe + assemble (existing logic, retargeted)

Unchanged in behavior, new output paths:

1. Per track: `silencedetect`, transcribe with `whisper-cli` (skip a fully-silent track,
   trailing-silence trim as today).
2. Render each track's SRT to `parts/<track>.txt` (`you.txt`, `caller.txt`, and `markers.txt` when
   present) via the existing single-track `merge_srt` path.
3. Weave you + caller into `<name>/<name>.txt`.
4. `casting_call.clean`: drop lines inside a known-silent span for their channel, plus the curated
   junk-phrase list.
5. `casting_call.markers`: fold recognized Track 3 phrases in as inline `[MARKER] <type>` lines.
6. Relabel Caller lines from `speakers.json` if present (unchanged `rips` integration).

## Components

| Unit | Change |
|---|---|
| `bin/extract_audio_stereo.sh` (`ripa`) | new per-recording folder layout; Phase 1 emits WAV + MP3 per track and a combined MP3; Phase 2 writes per-track transcripts to `parts/` and the merged transcript to `<name>/<name>.txt` |

No Python package changes. `clean.py`, `markers.py`, `transcript.py`, and the `speakers.json`
relabel run exactly as now, only on new paths.

## Breaking change

The merged transcript moves from `<folder>/transcripts/<name>.txt` to
`<folder>/<name>/<name>.txt`.

- `rips` / `ripcap` take an explicit `--transcript`, so they keep working; point them at the new
  path.
- The "already transcribed, skip" guard changes from checking `transcripts/<name>.txt` to checking
  `<name>/<name>.txt`, so re-running a folder stays safe and cheap.
- `ript` (batch) and the docs still assume `transcripts/`. Known and accepted, per Scope.

## Testing

This is a bash + ffmpeg change; the pytest suite does not cover it. Verify by:

- `bash -n bin/extract_audio_stereo.sh` for syntax.
- Running `ripa` on a fresh 3-track recording and confirming: one ffmpeg demux pass, the folder
  tree above exists, `<name>.txt` matches the current weaving / cleanup / marker behavior, and the
  per-track `parts/` files are present and non-empty.
- Spot-checking that `<name>.mp3` plays and contains all three tracks, and that a re-run of the
  folder skips the already-done recording.

## Known follow-ups

- `ript` (batch) and the README / user-guide still describe the `transcripts/` layout. Reconcile in
  a later pass.
- The `ripa` script filename still says "stereo"; rename to `transcribe_multitrack.sh` and repoint
  the alias when convenient.
