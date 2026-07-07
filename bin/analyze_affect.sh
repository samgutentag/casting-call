#!/bin/bash
# Measure how it was said: prosody from a speaker's audio channel, or
# smile/nod tracking from their video tile when the audio is missing.
#
# Usage (prosody): analyze_affect.sh <recording> --transcript t.txt [--label Caller]
# Usage (faces):   analyze_affect.sh <recording> --faces --tile x,y,w,h
#
# Dependencies: brew install ffmpeg
#               pip install --break-system-packages praat-parselmouth mediapipe

set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v ffmpeg &>/dev/null; then
    echo "Error: ffmpeg not found. Run: brew install ffmpeg"; exit 1
fi

cd "$REPO_DIR"
exec python3 -m casting_call.affect "$@"
