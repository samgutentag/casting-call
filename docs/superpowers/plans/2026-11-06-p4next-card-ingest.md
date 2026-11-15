# P4next Card Backup and Watch Video Plan

> **Status:** revised 2026-11-14 after the first P4next test call. Task 0 is done. Tasks 1 to 5 are approved (Q7, Q9) and build on top of the single-marker change (`feat/single-marker`, commit 2abe3f4).

**Goal:** OBS stays the main recording. The P4next's microSD card is the backup: when an OBS audio track drops, `ripa` fills the gap from the card, lined up to the video. `ripv` renders the long-term `.mp4` with the card's stereo mix as its soundtrack when a card take is present.

**Why:** OBS app audio capture fails silently. On 2026-11-06 the mic track was digital silence for a whole call, and on 2026-11-13 the Chrome capture stopped 12 minutes into one call and paused 81 seconds in another, with no error in the OBS log. The P4next now feeds OBS directly over USB, and it also writes every channel to its own card with no Mac software in the path.

**Tech stack:** bash (`bin/extract_audio_stereo.sh`, `bin/convert_video.sh`), ffmpeg, whisper-cli, the `casting_call` Python package. No new dependencies.

## The rig, as tested on 2026-11-14

P4next settings: Recording file type = Multi Track, USB Audio Interface Mode = Multi Track, Mix Minus = On, Play Key in Rec = Mark Only, pad A = `MARKER.WAV` (One-Shot). Mac input and output, and Meet's mic and speaker, are all `ZOOM P4next`. Meet uses USB channel 1 as the mic.

| P4next USB channel | Carries | OBS source (scene `Call P4`) | OBS tracks |
|---|---|---|---|
| 1 | mic (RØDE PodMic on INPUT 1) | `P4 Mic` (Ch 1 / Ch 1) | 1 Mix, 2 Mic |
| 7-8 | computer audio (every app) | `P4 Computer` (Ch 7 / Ch 8) | 1 Mix, 3 Other |
| 9-10 | sound pads | `P4 Marker` (Ch 9 / Ch 10) | 4 Notes |
| 11-12 | P4next master | not used | none |

Channels 11-12 leave out the computer audio because Mix Minus is On, so OBS builds track 1 itself. The OBS file keeps the same four-track layout `ripa` already reads.

Card take for one recording (the stereo mix sits at the card root, the rest in a folder):

| File | Holds |
|---|---|
| `<yymmdd_hhmmss>.wav` | stereo mix: mic, computer audio and pads |
| `P4next_Multitrack/<yymmdd_hhmmss>/<yymmdd_hhmmss>_MIC1.wav` | mic, mono |
| `.../<yymmdd_hhmmss>_USB.wav` | computer audio, stereo |
| `.../<yymmdd_hhmmss>_SOUND_PAD.wav` | pads, stereo |
| `.../<yymmdd_hhmmss>_MIC2.wav` to `_MIC4.wav` | unused inputs, digital silence (-91 dB) |

Every file is 48 kHz, 24-bit, with a `bext` chunk (date, start time, time reference in samples) and a `cue ` chunk holding PLAY-key marks as sample offsets. Files over 2 GB continue with a `_02` style suffix.

Decided and not up for change in this plan:

- One marker type, `marker`, from pad A. The Stream Deck is retired for calls.
- No Loopback, no 3.5 mm cable from the Mac.
- Mute in Meet, never with P4next MUTE 1, so muted notes stay on the card and on OBS track 2.
- The card stays in the P4next. Files come off through Menu > USB File Transfer, which mounts it as `/Volumes/P4NEXT_SD`.

## Task 0: First-call check (done 2026-11-14)

- [x] Five-minute test call recorded on both OBS and the card.
- [x] File names, folder layout, `bext` time reference and `cue ` marks recorded above.
- [x] The far side does not hear pad A (checked on a phone in the same Meet).
- [x] The far side is in the card's stereo mix at full level.
- [x] One press of pad A gives one burst on both OBS track 4 and the card's pad file. The card started 1.137 s before OBS, and every burst lines up at that offset.
- [x] New `ripa` (single-marker branch) transcribed both sides from the OBS file and saved 3840x2160 frames for each marker.
- [ ] Save the test call as a fixture: trimmed card files plus a trimmed audio-only copy of the `.mkv`, under `tests/fixtures/p4next/`. Moves into Task 1.

Open from the test: the first call produced 6 pad bursts for 2 intended presses, two of them about 0.7 s long instead of 0.4 s. A clean retest gave exactly 2. Watch for it on real calls; the 1.5 s merge window already absorbs double taps.

## Task 1: Copy and read card takes (`casting_call/card.py`)

- [ ] `copy_takes(day_dir)`: when `/Volumes/P4NEXT_SD` is mounted, copy every take whose `bext` start falls within a recording's span into `<name>/card/`, matched to the `.mkv` by start time. Never delete from the card. Print what was matched and what was left.
- [ ] `find_card_take(name_dir)`: read `<name>/card/`, map files to roles `mix`, `mic`, `usb`, `pads`. Unknown files are listed and ignored. MIC2 to MIC4 are ignored.
- [ ] Join `_02` split files per role with the ffmpeg concat demuxer.
- [ ] Read `bext` start time and `cue ` marks (sample offsets) with the standard library.
- [ ] Unit tests on the fixture: role mapping, split joining, `bext` and `cue ` parsing, no card folder.

## Task 2: Line the card up with the video (`casting_call/align.py`)

Output: one offset in seconds (card time = video time + offset), the method, and a confidence, written to `<name>/align.json`.

- [ ] **Markers, first choice:** match burst onsets on the card pad file against OBS track 4. On the test call every burst matched to the millisecond. Needs at least one press, so press pad A once right after starting both recorders.
- [ ] **Speech, second choice:** cross-correlate the speech envelopes of OBS track 2 and `MIC1` over the first 10 minutes at 1 kHz.
- [ ] **Clock, last choice:** card `bext` start time against the `.mkv` start time from its file name. Marked approximate.
- [ ] Below the confidence floor, say so in the transcript header instead of using it silently.
- [ ] Unit tests: synthetic offsets, no presses, dead OBS mic track.

The "Casting call sync" phrase and clap from the first plan are no longer needed. One pad press does the same job.

## Task 3: ripa fills OBS dropouts from the card

- [ ] Measure each OBS audio track first. A span of digital silence, or a silent stretch at or above the existing dropout thresholds, while the card role has signal for the same span, is a dropout.
- [ ] For each dropout, cut that span from the aligned card file (`MIC1` for track 2, `USB` for track 3), shifted onto the OBS timeline, and splice it into the track before transcription. Pad or trim, never stretch.
- [ ] If OBS track 4 is dead, take markers from the card pad file, shifted by the offset.
- [ ] Add PLAY-key marks from the card's `cue ` chunk as `[MARKER] marker` lines, with frames, same as pad presses. Drop a cue mark within 1.5 s of a pad press.
- [ ] Meet caption backup still runs, but only for spans the card cannot cover (no card take, or the card is silent too).
- [ ] The transcript header names the source of each track (OBS, card, or both with spans) and the alignment method.
- [ ] With no card take, run exactly as today.

## Task 4: Health report

- [ ] One line per track per recording: OBS level, any dropout spans, whether the card covered them.
- [ ] With no card take and a dead OBS track, say so loudly and point at the caption backup, as today.

## Task 5: Watch video in ripv (Q9)

`ripv` (`bin/convert_video.sh`) already renders `<name>/<name>.mp4`: H.265 at CRF 24, AAC 160k, `hvc1`, `+faststart`, about 40 times smaller than the `.mkv`.

- [ ] Audio source order:
  1. Card stereo mix, shifted by `align.json` (both sides at the levels set on the P4next, plus marker words).
  2. OBS track 1 (four-track files, including the `Call P4` scene).
  3. Legacy two-track files: today's left and right fold.
- [ ] When `align.json` is newer than an existing `.mp4`, re-render instead of skipping.
- [ ] Print which audio source was used.

The title-based detection of a new two-track layout from the first plan is dropped: the `Call P4` scene records four tracks.

## Open questions

- How much the P4next clock drifts against the Mac over an hour. Decides how far to trust the clock fallback, and whether long calls need a second offset at the end.
- Whether to delete the `.mkv` once the `.mp4` and the card take are safe. Sam's call, not part of this plan.

## Order and estimate

Tasks 1 and 2 together (about 1 h 15 min), Task 3 (about 45 min), Task 4 (15 min), Task 5 (30 min). Run the full test suite after Task 3 and after Task 5.
