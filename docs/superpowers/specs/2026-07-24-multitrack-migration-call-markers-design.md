# Multi-Track Migration + Call Markers: Design (as shipped)

**Project:** casting-call (`~/Developer/casting-call`)
**Date:** 2026-07-24
**Status:** Built and validated end to end on a real recording.
**Owner:** Sam Gutentag

> This doc was rewritten after implementation to describe what actually shipped. The pipeline
> moved from a single stereo recording to OBS multi-track, the affect layer was removed, and call
> markers ended up as an in-recording audio track rather than a sidecar file. The earlier draft
> (sidecar markers, `record_start` anchor, a custom Stream Deck plugin, mandatory MP4 remux) was
> superseded during the session and those pieces were retired.

## Problem

Two changes drove the work.

**The recording rig changed.** The old rig (Audio Hijack + Loopback + QuickTime) rerouted audio
*devices* through virtual devices, which dropped Sam's mic to Meet mid-call. It is retired. The new
rig is **OBS recording a multi-track `.mkv`**: screen video plus separate audio tracks. OBS only
reads audio, so nothing hijacks the mic.

**There was no way to flag moments during a call.** Sam wants to press a button mid-call and later
see exactly what was being said, with the flags handed to an LLM inline in the transcript.

## The recording

OBS records one `.mkv` per call, Output Mode Advanced, format MKV, recording tracks 1+2+3:

- **Track 1** (`0:a:0`) = Sam's mic (Elgato Wave).
- **Track 2** (`0:a:1`) = the far side (Google Chrome via macOS Application Audio Capture; Meet runs
  in Chrome, which captures more reliably than the PWA).
- **Track 3** (`0:a:2`) = **markers only**. Five OBS media sources (short spoken clips: "mark flag",
  "mark important", "mark action", "mark question", "mark quote") routed to Track 3 with Audio
  Monitoring off, so the far side never hears them. Each is fired by a Stream Deck **Media Source
  Control** action set to Restart.

The `.mkv` is the canonical, kept artifact. MKV is crash-safe to record, and every downstream tool
reads it directly, so there is no required remux.

## Locked decisions

1. **Multi-track is canonical; the stereo / pan-split path is deleted.** No auto-detect, no
   backward compatibility for old stereo `.mov` (they are already processed).
2. **No mandatory remux.** Transcription and OCR read the `.mkv`. A playable MP4 (video + you=L /
   caller=R stereo) is an *optional* render via `convert_video.sh` (`ripv`).
3. **Markers ride Track 3 inside the recording.** Same clock as the voice tracks, so there is no
   sidecar file and no alignment anchor. This retired the custom Stream Deck plugin and the old
   `markd.py` / `highlights.py` / `record_start` machinery.
4. **Markers are parsed by Whisper, not a tone detector.** Track 3 is transcribed like any track;
   recognized phrases become inline `[MARKER] <type>` lines at their own timestamp. Chosen over
   DTMF/tones because it reuses the pipeline and is ear-readable. Validated: `say` → Whisper →
   match round-trips correctly for all five types.
5. **The affect layer is removed.** Prosody and face tracking were more trouble than benefit; the
   modules, their tests, and the `praat-parselmouth` / `mediapipe` deps are gone.
6. **`callcheck` is retired**, its intent folded into a silence guard in the extract step.

## Pipeline (`ripa` / `extract_audio_stereo.sh`)

Per recording in a folder:

1. **Extract voice tracks** straight from the `.mkv`: `-map 0:a:0` / `0:a:1` to mono 16 kHz WAVs
   with `loudnorm`.
2. **Detect silence before transcribing** (`silencedetect`). A sustained continuous silence on the
   far-side track is warned about as a probable capture dropout (with a pointer to `ripcap`). A
   fully-silent track is skipped entirely.
3. **Transcribe** each non-silent track with `whisper-cli`.
4. **Weave** the two into `transcripts/<name>.txt` as `[h:mm:ss] [You]/[Caller]` lines.
5. **Clean hallucinations** (`casting_call.clean`): drop any line whose timestamp falls inside a
   known-silent span for its channel (Whisper invents speech on dead audio), plus a curated
   junk-phrase list ("Subtitles by the Amara.org community", etc.). Only phrases that never occur
   in a real call are stripped by phrase, so real speech is safe.
6. **Embed markers** (`casting_call.markers`): transcribe Track 3, match recognized phrases to
   types, and insert `[MARKER] <type>` lines inline at their offsets.
7. **Relabel** Caller lines from a caption-derived speaker timeline if a `speakers.json` exists
   (unchanged `rips` integration).

## Components

| Unit | Responsibility |
|---|---|
| `bin/extract_audio_stereo.sh` (`ripa`) | orchestrate extract → silence → transcribe → weave → clean → markers → relabel |
| `bin/convert_video.sh` (`ripv`) | optional playable MP4 render, you=L / caller=R |
| `casting_call/clean.py` | strip silent-span lines + junk phrases (tested) |
| `casting_call/markers.py` | parse Track-3 phrases, embed `[MARKER]` lines inline (tested) |
| `casting_call/transcript.py` | parse / render / relabel / strip helpers (tested) |
| `rips` / `ripcap` | caption OCR: speaker relabel, and dead-channel recovery (accept `.mkv`/`.mp4`) |

The label is `MARKER`, not `MARK`, to avoid colliding with a real speaker named Mark once Caller
lines are relabeled. Marker lines carry no emoji.

## Fallback: a dead far-side channel

Chrome's Application Audio Capture can drop mid-call and record silence. The silence guard flags it;
the words then live only in the on-screen Meet captions, which `ripcap` OCRs into a recovered
transcript. Known limitation, exposed by the first real recording: the caption `--region` is fixed,
so the Meet window must stay in one place and size for the whole call. Moving or resizing it means
recovering one layout at a time.

## Testing

- `casting_call/tests/` pytest suite, green with no ignores after the affect removal.
- `test_clean.py`: silent-span stripping (validated against the real dropout: 42 hallucinated lines
  removed) and junk-phrase filtering that leaves a real "thank you" alone.
- `test_markers.py`: phrase matching, timestamp/type extraction, inline embedding and sort order.
- End-to-end validated on a real 3-track recording: all five markers fired, recorded on Track 3,
  and embedded inline at the right moments.

## Known follow-ups

- The first marker phrase can get a `0:00:00` timestamp (a Whisper edge effect on the opening
  phrase); the rest align well.
- OBS does not write a reliable `creation_time`; irrelevant now that markers are in-recording, but
  worth knowing if any future feature wants file-level timing.
- The `ripa` script filename still says "stereo"; rename to `transcribe_multitrack.sh` and repoint
  the alias when convenient.
