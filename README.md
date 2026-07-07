# casting-call

Turn call recordings into transcripts that know who said what, and how they said it.

I record my calls as a stereo `.mov`: my mic on the left channel, everyone else on the
right, plus the Meet window on screen. This repo is everything that happens after the
call ends. It started as "split the channels and run Whisper" and has grown into four
capabilities that cover each way a recording can be useful (or broken).

**New here or coming back cold?** Read the [user guide](docs/user-guide.html) in a
browser. It is written to be read with zero prior knowledge, covers every command and
constant, and has animated diagrams of the three core mechanisms.

## The four capabilities

**1. Transcribe** (`ripa`). Split the stereo audio, run whisper-cpp on each channel,
merge by timestamp into `[h:mm:ss] [You] / [Caller]` lines. Every remote participant
lands on the right channel as one undifferentiated `Caller`.

**2. Attribute speakers** (`rips`). Meet's on-screen captions already say who is
speaking. Sample the video, OCR the caption band, fuzzy-match names against your local
roster, build a speaker timeline, and rewrite `Caller` lines with real names. Includes
an interactive review step for names it will not guess at.

**3. Recover from a dead channel** (`ripcap`). When the far-end audio never made it to
disk (it happens; ask me how I know, twice), the captions burned into the video are the
words. The stitcher OCRs every caption frame and merges the rolling, overlapping text
into one attributed transcript.

**4. Measure delivery** (`ripvibe`). A transcript flattens "that's valid" said warm and
"that's valid" said flat into the same seven characters. The affect layer measures per
utterance: pitch, energy, and pace from audio (via Praat), or smile intensity and nods
from the speaker's video tile (via MediaPipe) when their audio is gone. Everything is
z-scored against that speaker's own baseline, and the output is a list of moments worth
re-listening to, not a mind reader.

And one safety net for the *next* recording: **`callcheck`** verifies both channels are
actually capturing, either on an existing file (sampled scan, ~20s even on multi-GB
recordings) or live at the start of a call. `bin/callcheck_live.command` is a
Stream-Deck-ready front end: point a "System: Open" key at it and you get a 6-second
capture plus a PASS/FAIL notification before the call gets going.

## Commands

| Alias | Script | Does |
|---|---|---|
| `ripa` | `extract_audio_stereo.sh` | extract + transcribe + auto-relabel if a timeline exists |
| `rips` | `extract_speakers.sh` | caption OCR → speaker timeline → relabel transcript |
| `ripcap` | `stitch_captions.sh` | captions → full transcript (dead-channel fallback) |
| `ripvibe` | `analyze_affect.sh` | prosody z-scores, or `--faces` smile/nod tracking |
| `ript` | `transcribe_batch_stereo.sh` | batch transcribe across dated folders |
| `ripv` / `riprec` | `convert_video.sh` / `convert_recordings.sh` | compress video / audio+video housekeeping |
| `callcheck` | `callcheck.sh` | verify both channels (file, `--fast`, `--full`, or `--live`) |

The aliases live in my gutils shell config, not here. Every script works without them:
`bash bin/<script>.sh ...` from the repo root.

## Typical session

```bash
callcheck --live                                   # before the call: is the rig capturing both sides?
# ...record the call with QuickTime + Loopback...
ripa ~/calls/2026-07-06                            # transcribe everything in the folder
rips recording.mov --region 120,1850,900,180 \
     --transcript transcripts/recording.txt        # put real names on the Caller lines
ripvibe recording.mov \
     --transcript transcripts/recording.txt        # which moments deserve a re-listen
```

And when `callcheck` would have failed but you did not run it:

```bash
ripcap recording.mov --region 120,1850,900,180     # words, from the captions
ripvibe recording.mov --faces --tile 150,95,1055,920   # delivery, from their face
```

## Setup

```bash
brew install tesseract ffmpeg whisper-cpp
pip install --break-system-packages pillow numpy pytesseract pytest
# affect layer:
pip install --break-system-packages praat-parselmouth mediapipe
```

Whisper model (the batch scripts default to the smaller `-q5_0` variant; the error
message tells you which to fetch):

```bash
mkdir -p ~/whisper-models
curl -o ~/whisper-models/ggml-large-v3.bin -L \
  'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin?download=true'
```

Face landmark model (only for `ripvibe --faces`):

```bash
mkdir -p ~/.cache/casting-call
curl -sL -o ~/.cache/casting-call/face_landmarker.task \
  "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task"
```

Roster (your real names stay local; the file is gitignored):

```bash
cp speakers_roster.example.json speakers_roster.json
# edit: set "self" to your name, list the people you call
```

## Repo shape

```
bin/               shell entry points (thin; the logic lives in the package)
casting_call/      the Python package
  sample/locate/read/roster/timeline/transcript/review/coverage   speaker layer
  stitch.py        rolling-caption stitcher + CLI
  prosody.py       pitch/energy/pace features, within-speaker z-scores
  faces.py         smile blendshapes + nod detection
  affect.py        CLI orchestrator for prosody + faces
  tests/           pytest suite (fast, no fixtures on disk)
docs/user-guide.html   the real documentation; start there
```

Everything runs locally. No accounts, no uploads, and anything the tool cannot
confidently attribute stays labeled `Caller` rather than being guessed.
