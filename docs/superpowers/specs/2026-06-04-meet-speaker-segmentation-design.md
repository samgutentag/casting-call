# Google Meet Speaker Segmentation — Design

**Project:** casting-call (`~/Developer/casting-call`)
**Date:** 2026-06-04
**Status:** Design approved, pending spec review
**Owner:** Sam Gutentag

> **Repo scope:** casting-call owns the full call-recording pipeline. The existing media
> scripts (`extract_audio_stereo.sh`/`ripa`, `transcribe_batch_stereo.sh`/`ript`,
> `convert_video.sh`/`ripv`, `convert_recordings.sh`/`riprec`, and the non-stereo variants they
> call) migrate out of `gutils/scripts/` into this repo, joined by the new speaker-segmentation
> package. The speaker relabel becomes a native pipeline stage, not a cross-repo hook.

## Problem

The current call-recording pipeline (`extract_audio_stereo.sh`, alias `ripa`) records two
audio channels via Loopback: left = Sam's mic, right = everyone else ("Caller"). Whisper
transcribes each channel and merges them into a speaker-labeled transcript.

This works for 1:1 calls. It fails for group calls: the right channel mixes every remote
participant into one undifferentiated "Caller" track, so the transcript can't tell who said
what. Audio diarization (Whisper and others) was tried and rejected for poor, inconsistent
results.

## Key Insight

The recording is a full-screen QuickTime capture that includes the Google Meet window on
screen. **Meet's live captions already attribute each spoken line to a named speaker.** That
on-screen text is a deterministic speaker signal the app hands us for free — far more reliable
than acoustic diarization. We read the speaker *labels* from the captions to build a timeline
of who is speaking when, and keep Whisper for the actual transcript words (caption *text* is
too low-quality to use).

This was validated against a real recording
(`example-call.mov`): captions render speaker names (`You`, `Dana Park`) in a
consistent bottom strip of the Meet window, readable by OCR.

## Goals

- Produce a per-recording **active-speaker timeline** and use it to relabel the Caller-channel
  transcript segments with real speaker names.
- Match OCR'd names against a known **roster** of ~14 teammates; capture unknowns via a
  human-in-the-loop review step rather than guessing.
- Slot into the existing `scripts/` flow as a sibling tool with its own alias (`rips`), reusing
  the same `.mov` files `ripa` already consumes.

## Non-Goals

- Acoustic speaker diarization. We are using the visual signal instead.
- A new transcript engine. Whisper still owns the words; this only adds speaker attribution.
- Perfect attribution under heavy crosstalk. Ambiguous spans fall back to `[Caller]`.

## Constraints & Assumptions

- **Captions must be ON** during the recorded call. Sam runs them always / will make it a
  standard part of the recording setup.
- **Meet is a window, not full-screen.** Its position is stable within a single recording but is
  not guaranteed across recordings. The caption strip region must be *located*, not hardcoded.
- Recordings are high-res (4096×2304 Retina observed), so OCR has plenty of pixels to work with.
- Roster is small and closed (~14 names), turning OCR from open-ended recognition into
  pick-the-closest classification.

## Signal Strategy: captions primary, border deferred

The timeline is built by a **pluggable signal provider**. Two providers are defined; only the
first is implemented in v1.

1. **CaptionProvider (v1, implemented).** Reads Meet caption speaker-labels over time.
   Layout-independent (works in spotlight, tiled, sidebar) and scales to group calls for free —
   Meet labels every caption line regardless of participant count.
2. **BorderProvider (deferred, interface only).** Detects the active speaker by the blue
   "now speaking" tile border and OCRs the on-tile name. Layout-dependent, must handle grid
   reflow. Built only if captions are observed to drop turns in real use. The provider interface
   exists so this is a contained, additive change later.

This honors the "both" decision as an architectural seam without spending v1 effort on a
fallback that Sam's always-captions-on setup will rarely trigger.

## Architecture — isolated stages

Each stage is independently testable and communicates through files.

### 1. `sample`
ffmpeg extracts frames from the `.mov` at ~2 fps into a temp dir. Frame filenames encode the
source timestamp (e.g. `frame_000123.png` → 123 s ÷ fps). 2 fps is ample: speaking turns run
longer than 1 s and Meet captions persist for seconds.

### 2. `locate`
Find the caption strip region within the frame (Meet is windowed, so coordinates vary per
recording). v1 approach: detect the caption band's characteristic translucent-dark strip in the
lower portion of the Meet window, or accept a one-time per-recording region hint. Output: a crop
rectangle reused for all frames in that recording.

### 3. `read` (CaptionProvider)
For each sampled frame, OCR (tesseract) the caption strip. Parse caption lines into
`(speaker_label, text)` pairs — the speaker label is the styled name token that leads each line.
Take the newest (bottom-most) line's speaker as the active speaker at that frame's timestamp.
Dedup identical lines seen across consecutive frames into discrete speaker events.

**Validation gate (occlusion / tab-switch safety).** A frame only yields a speaker if the OCR'd
region actually *parses as a Meet caption*: a leading name token that matches the roster (or
`You`), plus the caption band's visual signature. When Meet is covered by another window or the
tab is switched away, the region OCRs to unrelated content (e.g. source code), fails the gate,
and the frame is marked **no signal** — never a fabricated speaker. Occlusion can produce a
*missing* label, never a *wrong* one.

Fuzzy-match each gate-passing speaker label against `speakers_roster.json`:
- Above match threshold → canonical roster name.
- `You` → Sam (roster self-entry).
- Below threshold → mark **unresolved** (do not force-match).

Debounce sub-1 s flickers. Emit `speakers.json`: an ordered list of spans
`[{start, end, name, confidence, raw_ocr, unresolved?}]`.

A **caption-lag offset** tuning constant accounts for captions trailing speech by ~1–2 s, so
caption-derived spans align with Whisper's audio-exact timestamps.

### 4. `review` (human-in-the-loop, only if unresolved spans exist)
Collect each *distinct* unresolved speaker. **Report the count up front** ("N distinct unknowns
to review") so Sam can decide whether it's worth it before starting. Dump one numbered cropped
frame per unknown (`unknown_01.png`, `unknown_02.png`, …) showing the caption (and, when
available, the tile) so Sam can identify them. Sam returns a number→name mapping. A `reconcile`
step writes those names back across all spans for that unknown.

**Bail-out:** at any point Sam can issue a "rest as Caller" command (e.g. an empty/`skip`
response, or after labeling only the few he cares about). Every still-unresolved unknown
immediately collapses to `[Caller]` and review ends — no requirement to clear the whole queue.
Anything Sam can't or won't resolve also falls back to `[Caller]`.

Resolved unknowns are candidates to append to `speakers_roster.json`, so the next call
recognizes them automatically — the review step shrinks over time.

### 5. `merge`
Modify the existing stereo merge step to consume `speakers.json` when present. For each
Caller-channel (right) Whisper segment, look up the active speaker at the segment's **midpoint**
timestamp (with lag offset applied) and relabel `[Caller]` → `[Name]`. The mic channel (left)
stays authoritative for Sam — when the timeline says Sam is active, right-channel audio is bleed
and is deferred to the left channel. Segments spanning two speakers are attributed by midpoint
and flagged. When no `speakers.json` exists, behavior is unchanged: plain `[Caller]`.

### Coverage reporting
At the end of a run, report how much of the call was attributed: e.g. "speaker attributed for
82% of call duration; 18% fell back to Caller (captions off / window covered / tab switched)."
Specifically flag stretches where the Caller channel *had* Whisper speech but there was no
on-screen signal — those are genuinely lost attributions, distinct from silence — so Sam can see
exactly what the tool missed rather than guessing.

## Data formats

### `speakers_roster.json`
```json
{
  "self": "Sam Gutentag",
  "members": [
    { "canonical": "Dana Park", "aliases": ["D Park", "Dana"] }
  ]
}
```
Editable by Sam. `self` maps Meet's `You` label. `aliases` improve fuzzy matching.

### `speakers.json` (per recording)
```json
[
  { "start": 12.0, "end": 47.5, "name": "Sam Gutentag", "confidence": 0.98, "raw_ocr": "You" },
  { "start": 47.5, "end": 63.0, "name": "Dana Park", "confidence": 0.91, "raw_ocr": "Dana Park" },
  { "start": 63.0, "end": 70.0, "name": null, "unresolved": true, "raw_ocr": "Jrdn M.", "review_id": "unknown_01" }
]
```

### Config (top of script)
fps, caption-strip locate strategy/hint, fuzzy-match threshold, debounce window, caption-lag
offset. The deferred BorderProvider adds a tunable target-blue (sampled from a real frame, never
hardcoded).

## Integration

- The full pipeline lives in `~/Developer/casting-call/bin/`. Existing scripts migrate there;
  their aliases (`ripa`/`ript`/`ripv`/`riprec`) are repointed in
  `gutils/oh-my-zsh-setup/gutils_aliases.zsh` to the new paths. New entry `extract_speakers.sh`,
  alias `rips`.
- New Python package `casting_call/` holds the speaker logic. `rips` produces `speakers.json`
  alongside the recording.
- The relabel becomes a native stage of the migrated `extract_audio_stereo.sh`: after the
  transcript is written, it calls `casting_call` to relabel `[Caller]` lines from
  `speakers.json` when present. No-op when absent (backward compatible).
- Alias definitions stay in gutils' shell config (sourced by `.zshrc`); only the target paths
  change. Update the `gutils` CLAUDE.md note that points at `scripts/` for media processing.

## Error handling / edge cases

- **Captions off / not found:** no `speakers.json`; transcript falls back to `[Caller]`.
  (Future: BorderProvider fallback.)
- **Meet covered / tab switched away:** frames fail the `read` validation gate → "no signal"
  → timeline gap → those segments fall back to `[Caller]`. Audio is unaffected (Loopback is
  independent of what's on screen), so words are never lost — only attribution. The system is
  strictly additive: occlusion degrades to today's `[Caller]` behavior, never worse.
- **Brief occlusion mid-turn:** a **grace-bridge** carries the last known speaker across short
  gaps (tunable, ~3 s) when the same speaker is active immediately before and after, so a quick
  alt-tab doesn't fragment one turn into `Name / Caller / Name`. Longer gaps are not bridged.
- **Sub-1 s flicker / crosstalk:** debounce window collapses noise.
- **Below-threshold OCR:** routed to `review`, never force-matched.
- **Segment spans two speakers:** midpoint attribution + flag.
- **Sam active on right channel:** treated as bleed, deferred to left channel.
- **Caption lag:** absorbed by the lag-offset constant + midpoint attribution.

## Dependencies

New (to flag): `tesseract` (OCR), Python `opencv` (frame/region handling). ffmpeg already
present. All local, free, offline. No API cost in v1.

## Testing

- `read` validated against a real recording with known turn order — eyeball `speakers.json`
  against the video.
- `merge` unit-tested with a synthetic Whisper SRT + hand-written timeline.
- **Tuning gate:** the caption-lag offset, debounce window, and match threshold are tuned
  against a real **group-call** recording before the tool is considered done. The 2-person
  `example-call.mov` sample validates plumbing but not the group case that motivates the work.

## Open items

- Obtain a representative group-call recording for tuning.
- Decide the `locate` strategy concretely (auto-detect caption band vs per-recording hint)
  during implementation, based on how stable the band is to detect.
