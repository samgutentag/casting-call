#!/bin/bash
# Build a caption-derived speaker timeline (speakers.json) for a Meet recording.
#
# Usage: extract_speakers.sh <recording.mp4> --region x,y,w,h [--out dir] [--transcript t.txt]
#
# Dependencies: brew install tesseract ffmpeg
#               pip install --break-system-packages pillow numpy pytesseract

set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v tesseract &>/dev/null; then
    echo "Error: tesseract not found. Run: brew install tesseract"; exit 1
fi

cd "$REPO_DIR"

# The OCR layer needs numpy, pillow, pytesseract. Use the default python3 if it
# has them; otherwise fall back to a Homebrew/local python that does.
PYTHON=python3
if ! "$PYTHON" -c 'import numpy, PIL, pytesseract' 2>/dev/null; then
    for alt in /opt/homebrew/bin/python3 /usr/local/bin/python3; do
        if [ -x "$alt" ] && "$alt" -c 'import numpy, PIL, pytesseract' 2>/dev/null; then
            PYTHON="$alt"; break
        fi
    done
fi
exec "$PYTHON" -m casting_call.cli "$@"
