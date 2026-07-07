#!/bin/bash
# Recover a full attributed transcript from on-screen Meet captions.
# The fallback for recordings where a channel's audio never made it to disk.
#
# Usage: stitch_captions.sh <recording.mov> --region x,y,w,h [--out dir] [--fps 1.5] [--jobs 8]
#
# Dependencies: brew install tesseract ffmpeg

set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v tesseract &>/dev/null; then
    echo "Error: tesseract not found. Run: brew install tesseract"; exit 1
fi
if ! command -v ffmpeg &>/dev/null; then
    echo "Error: ffmpeg not found. Run: brew install ffmpeg"; exit 1
fi

cd "$REPO_DIR"
exec python3 -m casting_call.stitch "$@"
