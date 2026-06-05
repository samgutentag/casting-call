#!/bin/bash

# Convert .mov recordings: extract audio + convert video
# Usage: convert_recordings.sh [directory]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT_DIR="${1:-.}"

echo "=================================="
echo ""

bash "$SCRIPT_DIR/extract_audio.sh" "$INPUT_DIR"

echo ""

bash "$SCRIPT_DIR/convert_video.sh" "$INPUT_DIR"

echo ""
echo "=================================="
echo "All processing complete!"
