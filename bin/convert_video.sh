#!/bin/bash

# OPTIONAL: render a smaller, playable .mp4 from a multi-track recording, with
# the two audio tracks folded into one stereo track: you = left, caller = right.
#
# The pipeline does NOT need this (transcription reads the .mkv directly). It is
# purely for watching a call back, or eyeballing captions. The .mkv stays the
# canonical recording; this is a compressed convenience copy you can keep around
# without eating the disk the raw recordings do.
#
# Video is re-encoded to H.265 (HEVC, CRF 24) — lossy but far smaller than the
# source, and playable in QuickTime/IINA on macOS (the hvc1 tag is what makes
# QuickTime recognize it). Audio is rebuilt into the L/R mix.
#
# Output lands in the same per-recording folder ripa creates: <name>/<name>.mp4,
# next to <name>.mp3 and the parts/ tracks. Run ripa first (or after) and each
# recording ends up as one self-contained folder.
#
# Usage: convert_video.sh [directory]

INPUT_DIR="${1:-.}"

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
    rec_dir="$INPUT_DIR/$filename"
    out="$rec_dir/${filename}.mp4"

    echo "[$current/$total] $filename"
    if [ -f "$out" ]; then
        echo "  ⏭  already rendered, skipping"; continue
    fi
    mkdir -p "$rec_dir"

    # Downmix each track to mono (handles mono or stereo tracks), place you on
    # the left channel and the caller on the right, and re-encode the video to
    # H.265 to shrink it. hvc1 tag keeps QuickTime happy; +faststart moves the
    # moov atom up for instant playback.
    ffmpeg -i "$input_file" \
        -filter_complex "[0:a:0]aformat=channel_layouts=mono[l];[0:a:1]aformat=channel_layouts=mono[r];[l][r]join=inputs=2:channel_layout=stereo[a]" \
        -map 0:v:0 -map "[a]" \
        -c:v libx265 -crf 24 -preset medium -tag:v hvc1 \
        -c:a aac -b:a 160k \
        -movflags +faststart \
        "$out" -y -loglevel error

    if [ -f "$out" ]; then
        in_size=$(du -h "$input_file" | cut -f1)
        out_size=$(du -h "$out" | cut -f1)
        echo "  ✓ Rendered → $out  ($in_size → $out_size)"
    else
        echo "  ✗ ffmpeg failed for $filename" >&2
    fi
done

echo ""
echo "Done. Each playable .mp4 is in its recording folder: $INPUT_DIR/<name>/<name>.mp4"
