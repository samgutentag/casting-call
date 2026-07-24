#!/bin/bash

# OPTIONAL: render a playable .mp4 from a multi-track recording, with the two
# audio tracks folded into one stereo track: you = left, caller = right.
#
# The pipeline does NOT need this (transcription reads the .mkv directly). It is
# purely for watching a call back, or eyeballing captions. The .mkv stays the
# canonical recording; this is a convenience copy.
#
# Video is stream-copied (no re-encode, no quality loss). Only the audio is
# rebuilt into the L/R mix. Output: converted_video/<name>.mp4
#
# Usage: convert_video.sh [directory]

INPUT_DIR="${1:-.}"

mkdir -p "$INPUT_DIR/converted_video"

shopt -s nullglob
recordings=("$INPUT_DIR"/*.mkv "$INPUT_DIR"/*.mp4)
shopt -u nullglob

total=${#recordings[@]}
current=0

echo "Found $total recording(s) to render"
echo "Audio layout: L=you (0:a:0)  R=caller (0:a:1)"
echo "--------------------------"

for input_file in "${recordings[@]}"; do
    filename=$(basename "$input_file"); filename="${filename%.*}"
    current=$((current + 1))
    out="$INPUT_DIR/converted_video/${filename}.mp4"

    echo "[$current/$total] $filename"
    if [ -f "$out" ]; then
        echo "  ⏭  already rendered, skipping"; continue
    fi

    # Downmix each track to mono (handles mono or stereo tracks), then place you
    # on the left channel and the caller on the right.
    ffmpeg -i "$input_file" \
        -filter_complex "[0:a:0]aformat=channel_layouts=mono[l];[0:a:1]aformat=channel_layouts=mono[r];[l][r]join=inputs=2:channel_layout=stereo[a]" \
        -map 0:v:0 -map "[a]" \
        -c:v copy -c:a aac -b:a 160k \
        -movflags +faststart \
        "$out" -y -loglevel error
    echo "  ✓ Rendered → $out"
done

echo ""
echo "Done. Playable videos in: $INPUT_DIR/converted_video/"
