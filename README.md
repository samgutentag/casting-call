# casting-call

Turn Google Meet call recordings into speaker-attributed transcripts.

Records a stereo `.mov` (left = your mic, right = everyone else) plus the on-screen Meet
window. Whisper transcribes the two audio channels; the speaker layer reads Meet's on-screen
caption labels to attribute the otherwise-undifferentiated "Caller" channel to named speakers.

**New here?** Read the [user guide](docs/user-guide.html) for a plain-language, read-cold tour of the whole pipeline: install, the commands, the caption region, the review step, and what gets written to disk. Open it in a browser.

## Pipeline (bin/)
- `extract_audio_stereo.sh` (`ripa`) — extract stereo audio + transcribe + relabel speakers
- `transcribe_batch_stereo.sh` (`ript`) — batch transcribe across date dirs
- `convert_video.sh` (`ripv`) — compress .mov to mp4
- `convert_recordings.sh` (`riprec`) — extract audio + convert video
- `extract_speakers.sh` (`rips`) — build the speaker timeline (speakers.json) from captions

## Speaker layer (casting_call/)
Python package: sample frames → locate caption band → OCR + roster-match → timeline →
review unknowns → relabel transcript. See `docs/superpowers/specs/`.

## Caption stitching (casting_call/stitch.py)
When a channel's audio is missing or unusable (run `callcheck` early!), the on-screen
captions themselves become the transcript. `stitch_frames()` OCRs every caption-band
frame and merges the rolling text via longest-overlap alignment into a full attributed
transcript. Speakers resolve through the same roster as the speaker layer.

## Affect layer (casting_call/prosody.py, casting_call/faces.py)
Transcripts say what was said; this layer measures how it was said.

- `prosody.py` — per-utterance pitch (median, range, terminal slope), energy, and pace
  from one speaker's audio channel, z-scored against that speaker's own baseline.
  Rising terminal pitch on a short affirmation reads warm; flat and quiet reads polite.
- `faces.py` — smile intensity (MediaPipe blendshapes) and nod detection (head-pitch
  oscillation) from a speaker's video tile, for when their audio never made it to disk.

Treat the output as a heatmap over the call — which moments deserve a re-listen —
not as ground truth. Affect inference is noisy at single-utterance level; the
within-speaker z-scores are the only honest comparison.

The face model is fetched once to `~/.cache/casting-call/face_landmarker.task`:

    curl -sL -o ~/.cache/casting-call/face_landmarker.task \
      "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task"

## Setup
    brew install tesseract ffmpeg whisper-cpp
    pip install --break-system-packages pillow numpy pytesseract pytest
    # affect layer:
    pip install --break-system-packages praat-parselmouth mediapipe

Then create your roster (your real names stay local — `speakers_roster.json` is gitignored):

    cp speakers_roster.example.json speakers_roster.json
    # edit speakers_roster.json: set "self" to your name and list the people you call

## Usage
    # build the speaker timeline for a recording (find the caption-strip region once per setup)
    rips recording.mov --region x,y,w,h
    # ...or relabel an existing transcript in place
    rips recording.mov --region x,y,w,h --transcript transcript.txt

On a 2-person call the tool auto-detects the single other speaker and attributes every Caller
line to them. Use `--callers N` to override (`--callers 2` forces per-turn timing on group calls).

## Tests
    python3 -m pytest casting_call/tests/ -v
