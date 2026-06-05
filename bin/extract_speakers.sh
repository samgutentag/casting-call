#!/bin/bash
# Build a caption-derived speaker timeline (speakers.json) for a Meet recording.
#
# Usage: extract_speakers.sh <recording.mov> --region x,y,w,h [--out dir] [--transcript t.txt]
#
# Dependencies: brew install tesseract ffmpeg
#               pip install --break-system-packages pillow numpy pytesseract

set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v tesseract &>/dev/null; then
    echo "Error: tesseract not found. Run: brew install tesseract"; exit 1
fi

cd "$REPO_DIR"
exec python3 -m casting_call.cli "$@"
