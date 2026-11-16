# casting-call

Turn call recordings into transcripts that know who said what, and how they said it.

I record my calls with OBS into a single `.mkv`: a master mix on Track 1, my mic on
Track 2, system audio (everything that isn't my mic or a marker: Chrome/Meet, Slack,
Zoom, Roam) on Track 3, and in-call markers on Track 4, plus the Meet window on screen. Recordings from before 2026-10-08 have
three tracks (mic, far side, markers) and no master; `ripa` and `ripv` handle both.
This repo is everything that happens after the call ends. It started as "split the
tracks and run Whisper" and has grown into three capabilities that cover each way a
recording can be useful (or broken).

**New here or coming back cold?** Read the [user guide](docs/user-guide.html) in a
browser. Setting the rig up on a new machine is the [setup guide](docs/setup-guide.html).
The user guide is written to be read with zero prior knowledge, covers every command and
constant, and has animated diagrams of the three core mechanisms.

## The three capabilities

**1. Transcribe** (`ripa`). Separate the audio tracks, transcribe each with Deepgram
nova-3 (whisper-cpp on this Mac when there is no key or a request fails), and merge by
timestamp into `[h:mm:ss] [You] / [Caller]` lines. The far side is diarized, so Deepgram
splits it into speakers. When the recording has video of a Meet call, `ripa` then reads
Meet's caption speaker names (no roster) and names each Deepgram speaker whose caption
words clearly belong to one person, e.g. `[Corey Weathers]`. Two voices under one Meet
participant (a voice demo, a shared room) come out as `[Ed Charbeneau (second voice)]`.
The transcript's `# transcript you:` / `# transcript caller:` header lines say which
engine ran for each track.
Unsure lines stay `Caller`. The unnamed transcript is kept in `parts/pre-names.txt`, the
names and every line's votes in `parts/speaker-names.json`. Any label other than `You`
or `MARKER` is the far side.

**2. Attribute speakers** (`rips`). Meet's on-screen captions already say who is
speaking. Sample the video, OCR the caption band, fuzzy-match names against your local
roster, build a speaker timeline, and rewrite `Caller` lines with real names. Includes
an interactive review step for names it will not guess at.

**3. Recover from a dead channel.** When the far-end audio never made it to disk (it
happens; ask me how I know, twice), the captions burned into the video are the words.
`ripa` does this on its own for Google Meet: any far-side silence of a minute or more
gets its captions read, and if they show someone else talking, their words are spliced
into the transcript as `[Caller]` lines (original kept in `parts/pre-caption-splice.txt`).
It finds the caption panel by Meet's red end-call button, so the window can move
mid-call. Captions are only a backup; a call with working audio never touches them.
`ripcap` still stitches a whole recording from captions by hand, given a fixed region.

## Markers

One button, one marker. Pad A on the Zoom PodTrak P4next plays a short clip that says
"marker" onto the P4next's sound pads channel, which OBS records as Track 4. Press it
whenever a moment matters and it shows up in the transcript as `[0:12:34] [MARKER] marker`.
Nobody on the call hears it, and there is no syncing step, because all the tracks share
one clock.

| Button | Type | Press it when |
|---|---|---|
| P4next pad A | `marker` | anything on the call is worth coming back to |

For every press, `ripa` also saves:

- a full-resolution PNG of the screen at that instant, `<name>/markers/marker-HHMMSS.png`,
  taken from the `.mkv` before `ripo` trashes it
- a row in `<name>/markers.json` with the press time, type and frame path

The process-transcript skill reads those to summarize the conversation around each press
(`casting_call.digest` gives `marker` presses a 90 second window either side) and to show
the screen grab next to it.

Pad presses are found by silence detection alone; Whisper is not involved. Two presses
within 1.5 seconds count as one, and clicks shorter than 0.15 seconds are ignored.

### Older recordings

Calls recorded before 2026-11-14 used seven Stream Deck keys (`topic`, `action-me`,
`action-them`, `important`, `question`, `quote`, `video`), each a spoken clip that Whisper
identifies. `ripa` still reads them: it transcribes the first three presses, and any
Stream Deck phrase there switches the whole track to the old seven-type parsing.
`MARKER_MODE=single` or `MARKER_MODE=legacy` forces one or the other.

Clip generation and P4next pad setup are in the [setup guide](docs/setup-guide.html).

## Commands

| Alias | Script | Does |
|---|---|---|
| `ripa` | `extract_audio_stereo.sh` | extract + transcribe + patch far-side dropouts from Meet captions + auto-relabel if a timeline exists. `MARKER_TRACK=<n>` reads markers from track n for a misrouted recording; `MARKER_MODE=single\|legacy` forces the marker rig; `CAPTION_BACKUP=0` skips the caption check; `CAPTION_NAMES=0` skips naming the far side from caption names |
| `rips` | `extract_speakers.sh` | caption OCR → speaker timeline → relabel transcript |
| `ripcap` | `stitch_captions.sh` | captions → full transcript (dead-channel fallback) |
| `ripv` | `convert_video.sh` | re-encode to a smaller H.265 playable copy, audio is the master mix (legacy files: you=L / caller=R) |
| `ripcard` | `pull_card.sh` | with the P4next in USB File Transfer: copy new card takes to `~/Desktop/P4next-takes/` (`CARD_INBOX` overrides), skip silent MIC2-4, eject, then tell you to exit File Transfer |

The aliases live in my gutils shell config, not here. Every script works without them:
`bash bin/<script>.sh ...` from the repo root.

`ripa` sends both tracks to Deepgram at once and reads Meet caption frames in the
background from the start, so an hour of call takes about as long as the slowest
single step instead of all of them added up. It ends with a table of step times and
the `/process-transcript` command to run next.

Styling is optional. With [gum](https://github.com/charmbracelet/gum) on the PATH and
`rich` importable (`pip install --break-system-packages rich`), `ripa` and `ripcard`
draw spinners, live status, progress bars and a summary table. Without them, or with
`RIPA_PLAIN=1`, they print plain text. `RIPA_PYTHON` picks the python3 for the helper
modules (a venv with rich, for example).

## P4next card takes

The P4next writes every channel to its microSD card as a backup of the OBS recording.
After a call: USB File Transfer on the P4next, `ripcard`, then exit File Transfer as soon
as it says so (in that mode the Mac hears no computer audio). For a call worth keeping,
move its take folder from `~/Desktop/P4next-takes/` into the day folder next to the
`.mkv` and rename it to match:

```
call_logs/2026-w47/2026-11-17/
  2026-11-17-corey-sync.mkv
  2026-11-17-corey-sync.card/      <- was P4next-takes/261117_095958/, files keep their names
```

`ripa` reads the take where it sits, lines it up with the video from the pad presses
(`align.json`), fills any OBS dropout from it, and `ripv` uses its stereo mix as the
watch-video soundtrack. An unrenamed take folder in the day folder is paired by start
time instead. Press pad A once right after starting both recorders.

## Typical session

```bash
# ...record the call with OBS (multi-track: mic, far side, markers)...
ripa ~/calls/2026-07-06                            # transcribe; names far-side lines from Meet captions
# only if you want roster-matched names instead:
rips recording.mkv --region 120,1850,900,180 \
     --transcript recording/recording.txt          # put real names on the Caller lines
```

When the far-side audio drops on a Meet call, `ripa` patches it from the captions by
itself. For anything else (another app, or rebuilding a whole call from captions):

```bash
ripcap recording.mkv --region 120,1850,900,180     # words, from the captions
```

## Setup

```bash
brew install tesseract ffmpeg whisper-cpp
pip install --break-system-packages pillow numpy pytesseract pytest
```

Deepgram key: put `DEEPGRAM_API_KEY=...` in `.env` at the repo root (gitignored), or
export it. Without one, `ripa` uses whisper.

Whisper model, the fallback (`ripa` defaults to `ggml-large-v3.bin`; the error message tells you
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

The `/process-transcript` Claude Code skill lives in this repo. Link it once per machine:

```bash
ln -sfn ~/Developer/casting-call/skills/process-transcript ~/.claude/skills/process-transcript
```

## Repo shape

```
bin/               shell entry points (thin; the logic lives in the package)
casting_call/      the Python package
  sample/locate/read/roster/timeline/transcript/review/coverage   speaker layer
  stitch.py        rolling-caption stitcher + CLI
  markers.py       marker-track phrases folded into the transcript
  digest.py        marker presses grouped into a per-call digest (JSON)
  tests/           pytest suite (fast, no fixtures on disk)
skills/process-transcript/   the /process-transcript skill (SKILL.md, render.py, report templates)
docs/user-guide.html   the real documentation; start there
docs/setup-guide.html  building the OBS + Stream Deck rig on a new machine
```

Transcription uploads each track to Deepgram, every request with `mip_opt_out=true` so
the audio stays out of Deepgram's Model Improvement Program. `TRANSCRIBER=whisper ripa`
keeps the whole run on this Mac. Everything else runs locally, and anything the tool
cannot confidently attribute stays labeled `Caller` rather than being guessed.
