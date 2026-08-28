# casting-call

Turn call recordings into transcripts that know who said what, and how they said it.

I record my calls with OBS into a single `.mkv`: my mic on one track, the far side
(Chrome/Meet) on a second, in-call markers on a third, plus the Meet window on screen.
This repo is everything that happens after the call ends. It started as "split the
tracks and run Whisper" and has grown into three capabilities that cover each way a
recording can be useful (or broken).

**New here or coming back cold?** Read the [user guide](docs/user-guide.html) in a
browser. Setting the rig up on a new machine is the [setup guide](docs/setup-guide.html).
The user guide is written to be read with zero prior knowledge, covers every command and
constant, and has animated diagrams of the three core mechanisms.

## The three capabilities

**1. Transcribe** (`ripa`). Separate the audio tracks, run whisper-cpp on each, merge
by timestamp into `[h:mm:ss] [You] / [Caller]` lines. Every remote participant lands on
the far-side track as one undifferentiated `Caller`.

**2. Attribute speakers** (`rips`). Meet's on-screen captions already say who is
speaking. Sample the video, OCR the caption band, fuzzy-match names against your local
roster, build a speaker timeline, and rewrite `Caller` lines with real names. Includes
an interactive review step for names it will not guess at.

**3. Recover from a dead channel** (`ripcap`). When the far-end audio never made it to
disk (it happens; ask me how I know, twice), the captions burned into the video are the
words. The stitcher OCRs every caption frame and merges the rolling, overlapping text
into one attributed transcript.

## Markers

Seven keys on a Stream Deck, each wired to a silent audio clip that lands on Track 3.
Press one during a call and it shows up in the transcript as a `[MARKER]` line at that
timestamp. Nobody on the call hears anything, and there is no syncing step, because all
three tracks share one clock.

| Key | Type | Press it when |
|---|---|---|
| Topic Switch | `topic` | the conversation moves to a new subject |
| Action For Me | `action-me` | you just picked up something to do |
| Action For Them | `action-them` | the far side owes you something |
| Important | `important` | it matters and fits nothing else |
| Question | `question` | you have a follow-up (say it into your muted mic right after) |
| Quote | `quote` | worth repeating verbatim |
| Video | `video` | something on screen worth a screenshot |

Both action clips say the word "action", so the parser checks "for them" and "for me"
before falling back to a bare `action` type. That fallback has no key. It only catches a
press where Whisper ate the trailing word, and it surfaces as owner-unknown instead of
landing on the wrong person's list (which is the failure you would never notice).

Wiring the OBS sources and the Stream Deck keys is in the
[setup guide](docs/setup-guide.html), including a migration section if you already ran the
old flag/action/follow set.

## Commands

| Alias | Script | Does |
|---|---|---|
| `ripa` | `extract_audio_stereo.sh` | extract + transcribe + auto-relabel if a timeline exists |
| `rips` | `extract_speakers.sh` | caption OCR → speaker timeline → relabel transcript |
| `ripcap` | `stitch_captions.sh` | captions → full transcript (dead-channel fallback) |
| `ripv` | `convert_video.sh` | re-encode to a smaller H.265 playable copy, audio mixed you=L / caller=R |

The aliases live in my gutils shell config, not here. Every script works without them:
`bash bin/<script>.sh ...` from the repo root.

## Typical session

```bash
# ...record the call with OBS (multi-track: mic, far side, markers)...
ripa ~/calls/2026-07-06                            # transcribe everything in the folder
rips recording.mkv --region 120,1850,900,180 \
     --transcript recording/recording.txt          # put real names on the Caller lines
```

And when the far-side audio never made it to disk:

```bash
ripcap recording.mkv --region 120,1850,900,180     # words, from the captions
```

## Setup

```bash
brew install tesseract ffmpeg whisper-cpp
pip install --break-system-packages pillow numpy pytesseract pytest
```

Whisper model (`ripa` defaults to `ggml-large-v3.bin`; the error message tells you
which to fetch if it is missing):

```bash
mkdir -p ~/whisper-models
curl -o ~/whisper-models/ggml-large-v3.bin -L \
  'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin?download=true'
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
  markers.py       Track 3 marker phrases folded into the transcript
  digest.py        marker presses grouped into a per-call digest (JSON)
  tests/           pytest suite (fast, no fixtures on disk)
docs/user-guide.html   the real documentation; start there
docs/setup-guide.html  building the OBS + Stream Deck rig on a new machine
```

Everything runs locally. No accounts, no uploads, and anything the tool cannot
confidently attribute stays labeled `Caller` rather than being guessed.
