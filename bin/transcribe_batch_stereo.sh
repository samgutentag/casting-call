#!/bin/bash

# Batch transcribe stereo MP3s across date subdirectories with speaker labels
# Assumes: left channel = your mic, right channel = call audio
# Expects structure: <root>/<date_dir>/audio_only/*.mp3
# Transcripts saved to: <root>/<date_dir>/transcripts/
#
# Usage: transcribe_batch_stereo.sh [root_dir] [whisper_model_path]
#
# Dependencies:
#   brew install whisper-cpp ffmpeg

ROOT_DIR="${1:-.}"
WHISPER_MODEL="${2:-$HOME/whisper-models/ggml-large-v3-q5_0.bin}"

YOU_LABEL="You"
CALLER_LABEL="Caller"

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

# Merge two SRT files into a single speaker-labeled transcript sorted by timestamp
merge_srt() {
    local srt_you="$1"
    local srt_caller="$2"
    local label_you="$3"
    local label_caller="$4"
    local out_file="$5"

    python3 - "$srt_you" "$srt_caller" "$label_you" "$label_caller" "$out_file" <<'EOF'
import sys
import re

def parse_srt(path, label):
    entries = []
    with open(path, 'r') as f:
        content = f.read()
    blocks = content.strip().split('\n\n')
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        match = re.match(r'(\d+:\d+:\d+,\d+)\s-->', lines[1])
        if not match:
            continue
        time_str = match.group(1).replace(',', '.')
        h, m, s = time_str.split(':')
        seconds = int(h) * 3600 + int(m) * 60 + float(s)
        text = ' '.join(lines[2:]).strip()
        if text:
            entries.append((seconds, time_str.replace('.', ','), label, text))
    return entries

srt_you, srt_caller, label_you, label_caller, out_file = sys.argv[1:]

entries = parse_srt(srt_you, label_you) + parse_srt(srt_caller, label_caller)
entries.sort(key=lambda x: x[0])

with open(out_file, 'w') as f:
    for _, time_str, label, text in entries:
        t = time_str.split(',')[0]
        f.write(f"[{t}] [{label}] {text}\n")

print(f"Merged {len(entries)} segments")
EOF
}

total=$(find "$ROOT_DIR" -path "*/audio_only/*.mp3" | wc -l | tr -d ' ')
current=0
skipped=0
failed=0

print_progress() {
    local step=$1 total_steps=$2 label=$3
    local bar_width=30
    local filled=$(( step * bar_width / total_steps ))
    local empty=$(( bar_width - filled ))
    local bar=""
    for ((i=0; i<filled; i++)); do bar+="█"; done
    for ((i=0; i<empty; i++)); do bar+="░"; done
    printf "\r  [%s] %s" "$bar" "$label"
}

echo "Root:  $ROOT_DIR"
echo "Model: $WHISPER_MODEL"
echo "Channels: left=[${YOU_LABEL}]  right=[${CALLER_LABEL}]"
echo "Found $total .mp3 files"
echo ""

find "$ROOT_DIR" -path "*/audio_only/*.mp3" | sort | while read -r mp3_file; do
    filename=$(basename "$mp3_file" .mp3)
    date_dir=$(dirname "$(dirname "$mp3_file")")
    transcripts_dir="$date_dir/transcripts"
    txt_out="$transcripts_dir/${filename}.txt"
    tmp_you="/tmp/whisper_${filename}_you.wav"
    tmp_caller="/tmp/whisper_${filename}_caller.wav"
    tmp_srt_you="/tmp/whisper_${filename}_you.srt"
    tmp_srt_caller="/tmp/whisper_${filename}_caller.srt"
    current=$((current + 1))

    echo "[$current/$total] $(basename "$date_dir")/$filename"

    if [ -f "$txt_out" ]; then
        echo "  ⏭  skipped (already done)"
        skipped=$((skipped + 1))
        continue
    fi

    mkdir -p "$transcripts_dir"

    print_progress 0 4 "splitting channels..."
    ffmpeg -i "$mp3_file" \
        -af "pan=mono|c0=c0" -ar 16000 -ac 1 -c:a pcm_s16le \
        "$tmp_you" -y >/dev/null 2>&1
    ffmpeg -i "$mp3_file" \
        -af "pan=mono|c0=c1" -ar 16000 -ac 1 -c:a pcm_s16le \
        "$tmp_caller" -y >/dev/null 2>&1

    print_progress 1 4 "transcribing [${YOU_LABEL}]..."
    GGML_METAL_PATH_RESOURCES="$WHISPER_METAL_RESOURCES" \
    "$WHISPER_BIN" \
        --model "$WHISPER_MODEL" \
        --output-srt \
        --output-file "/tmp/whisper_${filename}_you" \
        "$tmp_you" >/dev/null 2>&1

    print_progress 2 4 "transcribing [${CALLER_LABEL}]..."
    GGML_METAL_PATH_RESOURCES="$WHISPER_METAL_RESOURCES" \
    "$WHISPER_BIN" \
        --model "$WHISPER_MODEL" \
        --output-srt \
        --output-file "/tmp/whisper_${filename}_caller" \
        "$tmp_caller" >/dev/null 2>&1

    print_progress 3 4 "merging..."
    if [ -f "$tmp_srt_you" ] && [ -f "$tmp_srt_caller" ]; then
        merge_srt "$tmp_srt_you" "$tmp_srt_caller" "$YOU_LABEL" "$CALLER_LABEL" "$txt_out" > /dev/null
        print_progress 4 4 "done ✓"
    else
        print_progress 4 4 "failed ✗"
        failed=$((failed + 1))
    fi

    echo ""
    rm -f "$tmp_you" "$tmp_caller" "$tmp_srt_you" "$tmp_srt_caller"
done

echo ""
echo "Done!  $total files  |  $skipped skipped  |  $failed failed"