#!/bin/bash

# Convert .mov files to compressed MP4
# Usage: convert_video.sh [directory]

INPUT_DIR="${1:-.}"

mkdir -p "$INPUT_DIR/converted_video"

total=$(find "$INPUT_DIR" -maxdepth 1 -name "*.mov" | wc -l)
current=0

echo "Found $total .mov files for video conversion"
echo "--------------------------"

for input_file in "$INPUT_DIR"/*.mov; do
    [ -e "$input_file" ] || continue

    filename=$(basename "$input_file" .mov)
    current=$((current + 1))

    echo "[$current/$total] Converting video: $filename"
    ffmpeg -i "$input_file" \
        -vf "scale=-2:1080" \
        -c:v libx264 \
        -crf 26 \
        -preset veryfast \
        -c:a aac \
        -b:a 96k \
        -movflags +faststart \
        "$INPUT_DIR/converted_video/${filename}.mp4" \
        -y -loglevel error
    echo "  ✓ Done"
done

echo ""
echo "Video conversion complete!"
echo "Videos saved to: $INPUT_DIR/converted_video/"
