#!/bin/bash

# OPTIONAL: render a smaller, playable .mp4 from a multi-track recording.
#
# Audio, first one available:
#   1. The P4next card's stereo mix, when a card take sits next to the
#      recording (<name>.card/, or an unrenamed take folder paired by start
#      time) and ripa lined it up (<name>/align.json, usable). Both sides at
#      the levels set on the P4next, shifted onto the video timeline.
#   2. The master mix (Track 1) on four-track files (2026-10-08 on, including
#      the P4next "Call P4" scene).
#   3. Older two- and three-track files: you = left, caller = right.
#
# A .mp4 older than its align.json is rendered again, so running ripa after
# ripv still gets the card soundtrack.
#
# The pipeline does NOT need this (transcription reads the .mkv directly). It is
# purely for watching a call back, or eyeballing captions. The .mkv stays the
# canonical recording; this is a compressed convenience copy you can keep around
# without eating the disk the raw recordings do.
#
# Video is re-encoded to H.265 (HEVC, CRF 24) — lossy but far smaller than the
# source, and playable in QuickTime/IINA on macOS (the hvc1 tag is what makes
# QuickTime recognize it).
#
# Output lands in the same per-recording folder ripa creates: <name>/<name>.mp4,
# next to <name>.mp3 and the parts/ tracks. Run ripa first (or after) and each
# recording ends up as one self-contained folder.
#
# Usage: convert_video.sh [directory]

INPUT_DIR="${1:-.}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Absolute, because the card lookups below run from inside REPO_DIR.
if [ ! -d "$INPUT_DIR" ]; then
    echo "Error: directory not found: $INPUT_DIR"; exit 1
fi
INPUT_DIR="$(cd "$INPUT_DIR" && pwd)"

shopt -s nullglob
recordings=("$INPUT_DIR"/*.mkv "$INPUT_DIR"/*.mp4)
shopt -u nullglob

total=${#recordings[@]}
current=0

echo "Found $total recording(s) to render"
echo "Audio: card stereo mix if lined up, else master mix (Track 1) on 4-track files, else L=you R=caller"
echo "--------------------------"

for input_file in "${recordings[@]}"; do
    filename=$(basename "$input_file"); filename="${filename%.*}"
    current=$((current + 1))
    rec_dir="$INPUT_DIR/$filename"
    out="$rec_dir/${filename}.mp4"

    echo "[$current/$total] $filename"
    if [ -f "$out" ]; then
        if [ -f "$rec_dir/align.json" ] && [ "$rec_dir/align.json" -nt "$out" ]; then
            echo "  ↻  align.json is newer than the .mp4, rendering again"
        else
            echo "  ⏭  already rendered, skipping"; continue
        fi
    fi
    mkdir -p "$rec_dir"

    # Legacy files: downmix each voice track to mono (handles mono or stereo
    # tracks) and put you on the left, the caller on the right. Either way the
    # video is re-encoded to H.265 to shrink it. hvc1 tag keeps QuickTime happy;
    # +faststart moves the moov atom up for instant playback.
    n_tracks=$(ffprobe -v error -select_streams a -show_entries stream=index \
        -of csv=p=0 "$input_file" 2>/dev/null | grep -c .)
    card_in=(); extra_in=(); offset=""
    offset=$( cd "$REPO_DIR" && python3 -m casting_call.align offset "$rec_dir" 2>/dev/null )
    if [ -n "$offset" ]; then
        while IFS= read -r arg; do card_in+=("$arg"); done < <(
            cd "$REPO_DIR" && python3 -m casting_call.card input-args "$rec_dir" mix \
                --video "$input_file" )
    fi
    if [ "${#card_in[@]}" -gt 0 ]; then
        # card time = video time + offset: skip that much of the card, or delay
        # it when the card started after OBS. apad + -shortest ends it with the video.
        if awk -v o="$offset" 'BEGIN{exit !(o >= 0)}'; then
            card_in=(-ss "$offset" "${card_in[@]}"); afilt="apad"
        else
            afilt="adelay=$(awk -v o="$offset" 'BEGIN{printf "%d", -o*1000}'):all=1,apad"
        fi
        extra_in=("${card_in[@]}")
        # -map_chapters 0: ffmpeg reads the card's cue marks as chapters, in card
        # time, and would otherwise write them into the .mp4 as a data track.
        audio=(-filter_complex "[1:a:0]${afilt}[a]" -map "[a]" -shortest -map_chapters 0)
        source="card stereo mix (offset ${offset} s)"
    elif [ "${n_tracks:-0}" -ge 4 ]; then
        audio=(-map 0:a:0); source="OBS Track 1 (master mix)"
    elif [ "${n_tracks:-0}" -ge 2 ]; then
        audio=(-filter_complex "[0:a:0]aformat=channel_layouts=mono[l];[0:a:1]aformat=channel_layouts=mono[r];[l][r]join=inputs=2:channel_layout=stereo[a]" -map "[a]")
        source="legacy fold: you left, caller right"
    else
        audio=(-map "0:a:0?"); source="OBS Track 1"
    fi
    echo "  → Audio: $source"
    # Render beside the old file and swap on success, so a failed re-render
    # never costs the .mp4 that was already there.
    tmp_out="$rec_dir/.${filename}.rendering.mp4"
    ffmpeg -i "$input_file" "${extra_in[@]}" \
        -map 0:v:0 "${audio[@]}" \
        -c:v libx265 -crf 24 -preset medium -tag:v hvc1 \
        -c:a aac -b:a 160k \
        -movflags +faststart \
        "$tmp_out" -y -loglevel error

    if [ $? -eq 0 ] && [ -s "$tmp_out" ] && mv -f "$tmp_out" "$out"; then
        in_size=$(du -h "$input_file" | cut -f1)
        out_size=$(du -h "$out" | cut -f1)
        echo "  ✓ Rendered → $out  ($in_size → $out_size)"
    else
        rm -f "$tmp_out"
        echo "  ✗ ffmpeg failed for $filename" >&2
    fi
done

echo ""
echo "Done. Each playable .mp4 is in its recording folder: $INPUT_DIR/<name>/<name>.mp4"
