# P4next Card Ingest and Watch Video Plan

> **Status:** planned, not started. Holds until the Zoom PodTrak P4next arrives and one real call has been recorded on it. Nothing in here changes `ripa` or `ripv` before then.

**Goal:** Make the P4next's microSD card the source of truth for call audio. `ripa` reads the card's per-channel files, lines them up with the OBS screen recording, and transcribes them. `ripv` renders the long-term `.mp4` with the card's master mix as its soundtrack.

**Why:** OBS keeps losing the mic track. On 2026-11-06 the devrel sync recorded the Elgato Wave as digital silence (-91 dB) for the whole call, and Sam's lines had to be recovered from Meet captions. The P4next writes every channel to its own card with no Mac software in the path.

**Tech stack:** bash (`bin/extract_audio_stereo.sh`, `bin/convert_video.sh`), ffmpeg, whisper-cli, the `casting_call` Python package. No new dependencies.

## The rig this plan assumes

| Where | Track | Holds | Source |
|---|---|---|---|
| Card | stereo mix file | everything | P4next MASTER |
| Card | `MIC1.WAV` | Sam's mic only | INPUT 1, RØDE PodMic (XLR) |
| Card | `USB.WAV` | computer audio only | P4next USB channel |
| Card | sound pads file | marker beeps only | pad A, One-Shot |
| OBS `.mkv` | track 1 (`0:a:0`) | Sam's mic | `ZOOM P4next` USB Stereo Mix (inputs 1 to 4) |
| OBS `.mkv` | track 2 (`0:a:1`) | computer audio, a convenience copy | OBS macOS audio capture |

Decided and not up for change in this plan:

- No Loopback until it is a hard requirement.
- No 3.5 mm cable from the Mac.
- Sam mutes in Meet, never with the P4next MUTE 1 button, so muted-mic notes stay on the card.
- Sync habit: once both recorders run, Sam says "Casting call sync" (muted in Meet) and claps once.
- PLAY key is set to Mark Only, so a silent mark can also land in the card files.

## In the meantime: ripa keeps working with no changes

`ripa` picks its stream layout from the audio track count. A 2-track OBS file takes the existing else branch: `you_s=0`, `cal_s=1`, no marker track. That matches the new OBS layout (mic on track 1, computer audio on track 2), so `ripa` transcribes new-rig calls from the `.mkv` alone on day one, with two gaps:

- No markers, because the beep only exists on the card.
- If OBS track 1 drops, the gap is back. The card fixes that once this plan ships.

`ripv` needs one fix before it is safe on new-rig files, covered in Task 5: its 2-track branch assumes the legacy layout and folds the tracks into left and right channels.

## Task 0: First-call check (no code)

- [ ] Record a 5 minute test call on the P4next with the full checklist from the call rig page.
- [ ] Run `ripa` on the OBS `.mkv` as-is and confirm it transcribes both sides.
- [ ] From the card, record the exact file names in the MultiTrack folder (the manual says "date_time_input"), the sound pads file name, and whether files carry a BWF `bext` time reference.
- [ ] Check whether PLAY-key marks are stored as WAV `cue ` chunks, and in which files.
- [ ] Confirm the far side does not hear pad A, and that OBS track 1 has the voice and no beep.
- [ ] Save the test call as a fixture (trimmed card files plus a trimmed `.mkv` audio-only copy) under `tests/fixtures/p4next/`.

Everything below uses the facts from this task. If any fact differs from what is written here, update this plan first.

## Task 1: Find and read the card folder (`casting_call/card.py`)

- [ ] `find_card_take(mkv_path)`: look for a copied card folder next to the `.mkv` (`<name>/card/` or any `MultiTrack/<date_time>/` folder in the call folder). If more than one take exists, pick the one whose start time is closest to the `.mkv` creation time, and say which one it picked.
- [ ] Map files to roles: `mix`, `mic`, `usb`, `pads`. Unknown files are listed and ignored, never guessed at.
- [ ] Join 2 GB split files into one stream per role (`ffmpeg` concat demuxer). Splits start at about 1 h 56 min for stereo files; no call since 2026-09-01 has gone past 1 h 01 min, so this is a safety net.
- [ ] Unit tests: role mapping, split joining, multiple takes, missing role.

## Task 2: Line the card up with the video (`casting_call/align.py`)

Output: one offset in seconds (card time = video time + offset) plus how it was found, written to `<name>/align.json`.

- [ ] **Phrase:** run whisper with word timestamps on the first 180 s of OBS track 1 and of `MIC1.WAV`. Find "casting call sync" in both. The difference is the coarse offset.
- [ ] **Clap:** within 3 s after the phrase in each file, take the sharpest onset peak. Refine the offset to the sample.
- [ ] **No phrase:** cross-correlate the speech envelopes of OBS track 1 and `MIC1.WAV` over the first 10 minutes at 1 kHz.
- [ ] **No usable OBS audio:** fall back to the card file start time (BWF time reference or file name) against the `.mkv` creation time. Mark the result as approximate.
- [ ] Every result carries its method and a confidence. Below the confidence floor, say so in the transcript header rather than silently using it.
- [ ] Unit tests: synthetic offsets, with and without phrase, with a dead OBS track.

## Task 3: Teach ripa to use the card

- [ ] At the start of each recording, call `find_card_take`. With no card take, run exactly as today.
- [ ] With a card take: build the you and caller WAVs from `MIC1.WAV` and `USB.WAV`, shifted by the offset onto the OBS timeline (pad or trim the head, never stretch), so every timestamp still matches the video.
- [ ] Combined mp3 comes from the card mix, not an amix.
- [ ] Markers: the pads file is silence plus one short beep per press, so detect presses with the existing `silencedetect` path and skip whisper. Add PLAY-key marks from the WAV cue chunk if Task 0 found them.
- [ ] Keep the caption backup step. It now only runs if the card is missing.
- [ ] Transcript header says which source each track came from (card or OBS) and the alignment method.

## Task 4: Health check on OBS tracks

- [ ] Before transcribing, measure each OBS audio track. A track at digital silence while the card has signal for the same role is logged as "OBS dropped, used card".
- [ ] When there is no card take and OBS track 1 is silent, say so loudly and point at the caption backup, as today.

## Task 5: Watch video in ripv (Q9)

`ripv` (`bin/convert_video.sh`) already renders a small `.mp4` next to the recording folder: H.265 at CRF 24, AAC 160k, `hvc1` tag, `+faststart`. Real sizes: 18 to 28 GB `.mkv` files become 300 to 750 MB `.mp4` files, about 40 times smaller. Q9 is a change to `ripv`, not a new tool.

- [ ] Audio source order:
  1. Aligned card mix from `<name>/align.json` (both sides plus marker beeps, mixed by the P4next).
  2. OBS master track on 4-track files (today's rig, 2026-10-08 on).
  3. New-rig 2-track OBS file: mix track 1 and track 2 to stereo with `amix`. Tell new-rig files apart from legacy 2-track files by OBS track titles (set "Mic" and "Computer" in OBS), not by count alone.
  4. Legacy 2-track files: keep today's left and right fold.
- [ ] Output stays `<name>/<name>.mp4`, MP4 container, so it stays the file Sam keeps long term.
- [ ] When `align.json` is newer than an existing `.mp4`, re-render instead of skipping.
- [ ] Print which audio source was used.

## Open questions

- Exact card file names and the sound pads file name (Task 0).
- Whether PLAY-key marks are WAV cue chunks (Task 0).
- How much the P4next clock drifts against the Mac, which decides how much to trust the start-time fallback.
- Whether to delete the `.mkv` once the `.mp4` and the card files are safe. That is Sam's call and not part of this plan.

## Order and estimate

Task 0 the day the P4next arrives (30 min). Then Tasks 1 to 3 together (about 2 h), Task 4 (30 min), Task 5 (about 30 min). Run the full test suite after Task 3 and after Task 5.
