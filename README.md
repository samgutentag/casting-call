# casting-call

Turn Google Meet call recordings into speaker-attributed transcripts.

Records a stereo `.mov` (left = your mic, right = everyone else) plus the on-screen Meet
window. Whisper transcribes the two audio channels; the speaker layer reads Meet's on-screen
caption labels to attribute the otherwise-undifferentiated "Caller" channel to named speakers.

## Pipeline (bin/)
- `extract_audio_stereo.sh` (`ripa`) — extract stereo audio + transcribe + relabel speakers
- `transcribe_batch_stereo.sh` (`ript`) — batch transcribe across date dirs
- `convert_video.sh` (`ripv`) — compress .mov to mp4
- `convert_recordings.sh` (`riprec`) — extract audio + convert video
- `extract_speakers.sh` (`rips`) — build the speaker timeline (speakers.json) from captions

## Speaker layer (casting_call/)
Python package: sample frames → locate caption band → OCR + roster-match → timeline →
review unknowns → relabel transcript. See `docs/superpowers/specs/`.

## Setup
    brew install tesseract ffmpeg whisper-cpp
    pip install --break-system-packages pillow numpy pytesseract pytest

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
