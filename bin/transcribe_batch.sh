#!/bin/bash

# Batch transcribe MP3s across date subdirectories
# Expects structure: <root>/<date_dir>/audio_only/*.mp3
# Transcripts saved to: <root>/<date_dir>/transcripts/
#
# Usage: transcribe_batch.sh [root_dir] [whisper_model_path]
#
# Dependencies:
#   brew install whisper-cpp ffmpeg

ROOT_DIR="${1:-.}"
WHISPER_MODEL="${2:-$HOME/whisper-models/ggml-large-v3-q5_0.bin}"

WHISPER_BIN="whisper-cli"
WHISPER_METAL_RESOURCES="$(brew --prefix whisper-cpp)/share/whisper-cpp"

# Check dependencies
if ! command -v "$WHISPER_BIN" &>/dev/null; then
    echo "Error: $WHISPER_BIN not found. Run: brew install whisper-cpp"
    exit 1
fi
if ! command -v ffmpeg &>/dev/null; then
    echo "Error: ffmpeg not found. Run: brew install ffmpeg"
    exit 1
fi
if [ ! -f "$WHISPER_MODEL" ]; then
    echo "Error: Whisper model not found at: $WHISPER_MODEL"
    exit 1
fi

total=$(find "$ROOT_DIR" -path "*/audio_only/*.mp3" | wc -l | tr -d ' ')
current=0

echo "Root:  $ROOT_DIR"
echo "Model: $WHISPER_MODEL"
echo "Found $total .mp3 files across subdirectories"
echo "--------------------------------"

find "$ROOT_DIR" -path "*/audio_only/*.mp3" | sort -r | tr -d '\r' | while read -r mp3_file; do
    # Resolve to absolute path and strip any stray whitespace
    mp3_file=$(realpath "$mp3_file" 2>/dev/null || echo "$mp3_file")
    mp3_file="${mp3_file//[$'\t\r\n']/}"
    filename=$(basename "$mp3_file" .mp3)
    date_dir=$(dirname "$(dirname "$mp3_file")")  # go up from audio_only/ to date_dir/
    transcripts_dir="$date_dir/transcripts"
    txt_out="$transcripts_dir/${filename}.txt"
    tmp_wav="/tmp/whisper_${filename}.wav"
    current=$((current + 1))

    # Safety check — skip if path looks wrong
    if [[ "$date_dir" != "$ROOT_DIR"* ]]; then
        echo "[$current/$total] SKIPPED (bad path: $date_dir)"
        continue
    fi

    echo "[$current/$total] $(basename "$date_dir") / $filename"

    if [ -f "$txt_out" ]; then
        echo "  ⏭  Already transcribed, skipping"
        continue
    fi

    mkdir -p "$transcripts_dir"

    # Convert MP3 to 16kHz mono WAV for whisper-cli
    ffmpeg -i "$mp3_file" \
        -ar 16000 -ac 1 -c:a pcm_s16le \
        "$tmp_wav" \
        -y >/dev/null 2>&1

    # Transcribe (GGML_METAL_PATH_RESOURCES enables GPU acceleration)
    GGML_METAL_PATH_RESOURCES="$WHISPER_METAL_RESOURCES" \
    "$WHISPER_BIN" \
        --model "$WHISPER_MODEL" \
        --output-txt \
        --output-file "$transcripts_dir/${filename}" \
        "$tmp_wav" >/dev/null 2>&1

    rm -f "$tmp_wav"

    if [ -f "$txt_out" ]; then
        word_count=$(wc -w < "$txt_out" | tr -d ' ')
        echo "  ✓ Done (~$word_count words)"
    else
        echo "  ✗ Failed"
    fi
done

echo ""
echo "Complete! Transcripts saved alongside each audio_only/ folder."