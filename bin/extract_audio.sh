#!/bin/bash

# Extract audio from .mov files as MP3 and transcribe with whisper-cpp
# Usage: extract_audio.sh [directory] [whisper_model_path]
#
# Dependencies:
#   brew install whisper-cpp ffmpeg
#
# Model download (one-time):
#   curl -o ~/whisper-models/ggml-large-v3-q5_0.bin -L \
#     'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-q5_0.bin?download=true'

INPUT_DIR="${1:-.}"
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
    echo ""
    echo "Download it with:"
    echo "  mkdir -p ~/whisper-models"
    echo "  curl -o ~/whisper-models/ggml-large-v3-q5_0.bin -L \\"
    echo "    'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-q5_0.bin?download=true'"
    exit 1
fi

mkdir -p "$INPUT_DIR/audio_only"
mkdir -p "$INPUT_DIR/transcripts"

total=$(find "$INPUT_DIR" -maxdepth 1 -name "*.mov" | wc -l)
current=0

echo "Found $total .mov files"
echo "Model: $WHISPER_MODEL"
echo "--------------------------------"

for input_file in "$INPUT_DIR"/*.mov; do
    [ -e "$input_file" ] || continue

    filename=$(basename "$input_file" .mov)
    current=$((current + 1))
    mp3_out="$INPUT_DIR/audio_only/${filename}.mp3"
    txt_out="$INPUT_DIR/transcripts/${filename}.txt"
    tmp_wav="/tmp/whisper_${filename}.wav"

    echo "[$current/$total] $filename"

    # Skip if transcript already exists
    if [ -f "$txt_out" ]; then
        echo "  ⏭  Transcript already exists, skipping"
        continue
    fi

    # Step 1: Extract MP3
    echo "  → Extracting audio..."
    ffmpeg -i "$input_file" \
        -vn \
        -c:a libmp3lame \
        -q:a 4 \
        "$mp3_out" \
        -y -loglevel error

    # Step 2: Convert to 16kHz mono WAV for whisper-cpp
    echo "  → Converting for transcription..."
    ffmpeg -i "$input_file" \
        -ar 16000 -ac 1 -c:a pcm_s16le \
        "$tmp_wav" \
        -y -loglevel error

    # Step 3: Transcribe
    echo "  → Transcribing..."
    GGML_METAL_PATH_RESOURCES="$WHISPER_METAL_RESOURCES" \
    "$WHISPER_BIN" \
        --model "$WHISPER_MODEL" \
        --output-txt \
        --output-file "$INPUT_DIR/transcripts/${filename}" \
        "$tmp_wav" \
        2>/dev/null

    # Clean up temp WAV
    rm -f "$tmp_wav"

    if [ -f "$txt_out" ]; then
        word_count=$(wc -w < "$txt_out")
        echo "  ✓ Done (~$word_count words)"
    else
        echo "  ✗ Transcription failed"
    fi
done

echo ""
echo "Complete!"
echo "  Audio  → $INPUT_DIR/audio_only/"
echo "  Transcripts → $INPUT_DIR/transcripts/"