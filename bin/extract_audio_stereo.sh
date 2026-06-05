#!/bin/bash

# Extract audio from stereo .mov files and transcribe with speaker labels
# Assumes: left channel = your mic, right channel = call audio
#
# Two-pass approach:
#   Pass 1 — rip all audio (fast, lets you work with files immediately)
#   Pass 2 — transcribe all extracted audio
#
# Usage: extract_audio_stereo.sh [directory] [whisper_model_path]
#
# Output per file:
#   audio_only/filename.mp3       — full stereo mix
#   transcripts/filename.txt      — merged transcript with speaker labels
#
# Dependencies:
#   brew install whisper-cpp ffmpeg
#
# Model download (one-time):
#   curl -o ~/whisper-models/ggml-large-v3.bin -L \
#     'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin?download=true'

INPUT_DIR="${1:-.}"
WHISPER_MODEL="${2:-$HOME/whisper-models/ggml-large-v3.bin}"

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
if ! command -v ffprobe &>/dev/null; then
    echo "Error: ffprobe not found (should come with ffmpeg)"
    exit 1
fi
if [ ! -f "$WHISPER_MODEL" ]; then
    echo "Error: Whisper model not found at: $WHISPER_MODEL"
    echo ""
    echo "Download it with:"
    echo "  mkdir -p ~/whisper-models"
    echo "  curl -o ~/whisper-models/ggml-large-v3.bin -L \\"
    echo "    'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin?download=true'"
    exit 1
fi

mkdir -p "$INPUT_DIR/audio_only"
mkdir -p "$INPUT_DIR/transcripts"

# Get audio duration in seconds
get_duration() {
    ffprobe -v error -show_entries format=duration -of csv=p=0 "$1" 2>/dev/null | cut -d. -f1
}

# Write the progress bar script to a temp file (avoids heredoc/stdin conflict when piping)
PROGRESS_SCRIPT=$(mktemp /tmp/whisper_progress_XXXX.py)
trap 'rm -f "$PROGRESS_SCRIPT"' EXIT
cat > "$PROGRESS_SCRIPT" <<'PYEOF'
import sys, re

label = sys.argv[1]
duration = float(sys.argv[2])
bar_width = int(sys.argv[3])

def parse_time(t):
    parts = t.replace(',', '.').split(':')
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return 0

def format_time(s):
    m, s = divmod(int(s), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"

pattern = re.compile(r'\[\s*[\d:.,]+\s*-->\s*([\d:.,]+)\s*\]')

last_pct = -1
for line in sys.stdin:
    match = pattern.search(line)
    if not match:
        continue
    current = parse_time(match.group(1))
    pct = min(current / duration, 1.0)
    int_pct = int(pct * 100)
    if int_pct == last_pct:
        continue
    last_pct = int_pct
    filled = int(bar_width * pct)
    bar = '\u2588' * filled + '\u2591' * (bar_width - filled)
    sys.stdout.write(f"\r  \u2192 {label} [{bar}] {int_pct}% ({format_time(current)}/{format_time(duration)})")
    sys.stdout.flush()

bar = '\u2588' * bar_width
sys.stdout.write(f"\r  \u2192 {label} [{bar}] 100% ({format_time(duration)}/{format_time(duration)})\n")
sys.stdout.flush()
PYEOF

# Parse whisper-cli stdout and render a progress bar
# Usage: whisper_cmd | whisper_progress "label" duration_seconds
whisper_progress() {
    local label="$1"
    local duration="$2"
    local bar_width=30

    if [ -z "$duration" ] || [ "$duration" -eq 0 ] 2>/dev/null; then
        local spin=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
        local i=0
        while IFS= read -r line; do
            printf "\r  → %s [%s] transcribing..." "$label" "${spin[$((i % 10))]}"
            i=$((i + 1))
        done
        printf "\r  → %s ✓%-40s\n" "$label" ""
        return
    fi

    python3 "$PROGRESS_SCRIPT" "$label" "$duration" "$bar_width"
}

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

total=$(find "$INPUT_DIR" -maxdepth 1 -name "*.mov" | wc -l)

echo "Found $total .mov files"
echo "Model: $WHISPER_MODEL"
echo "Channels: left=[${YOU_LABEL}]  right=[${CALLER_LABEL}]"
echo ""

# ── Pass 1: Extract all audio ──────────────────────────────────────────────────
echo "═══ Pass 1: Extracting audio ═══"
current=0
for input_file in "$INPUT_DIR"/*.mov; do
    [ -e "$input_file" ] || continue

    filename=$(basename "$input_file" .mov)
    current=$((current + 1))
    mp3_out="$INPUT_DIR/audio_only/${filename}.mp3"

    echo "[$current/$total] $filename"

    if [ -f "$mp3_out" ]; then
        echo "  ⏭  Audio already exists, skipping"
        continue
    fi

    echo "  → Extracting stereo MP3..."
    ffmpeg -i "$input_file" \
        -vn -c:a libmp3lame -q:a 4 \
        "$mp3_out" \
        -y -loglevel error

    echo "  ✓ Done"
done

echo ""
echo "All audio extracted → $INPUT_DIR/audio_only/"
echo ""

# ── Pass 2: Transcribe all files ────────────────────────────────────────────────
echo "═══ Pass 2: Transcribing ═══"
current=0
for input_file in "$INPUT_DIR"/*.mov; do
    [ -e "$input_file" ] || continue

    filename=$(basename "$input_file" .mov)
    current=$((current + 1))
    txt_out="$INPUT_DIR/transcripts/${filename}.txt"
    tmp_you="/tmp/whisper_${filename}_you.wav"
    tmp_caller="/tmp/whisper_${filename}_caller.wav"
    tmp_srt_you="/tmp/whisper_${filename}_you.srt"
    tmp_srt_caller="/tmp/whisper_${filename}_caller.srt"

    echo "[$current/$total] $filename"

    if [ -f "$txt_out" ]; then
        echo "  ⏭  Transcript already exists, skipping"
        continue
    fi

    # Split channels to mono 16kHz WAVs for whisper
    # loudnorm normalizes audio levels — quiet recordings get boosted to a consistent volume
    echo "  → Splitting channels..."
    ffmpeg -i "$input_file" \
        -af "pan=mono|c0=c0,loudnorm=I=-16:TP=-1.5:LRA=11" -ar 16000 -ac 1 -c:a pcm_s16le \
        "$tmp_you" -y -loglevel error

    ffmpeg -i "$input_file" \
        -af "pan=mono|c0=c1,loudnorm=I=-16:TP=-1.5:LRA=11" -ar 16000 -ac 1 -c:a pcm_s16le \
        "$tmp_caller" -y -loglevel error

    # Get channel durations for progress bars
    dur_you=$(get_duration "$tmp_you")
    dur_caller=$(get_duration "$tmp_caller")

    # Transcribe each channel
    # --beam-size 5: explore multiple decoding paths for better accuracy on noisy audio
    # --entropy-thold 2.4: filter high-entropy (low confidence) segments
    # -mc 0: disable context carryover to prevent hallucination loops
    GGML_METAL_PATH_RESOURCES="$WHISPER_METAL_RESOURCES" \
    "$WHISPER_BIN" \
        --model "$WHISPER_MODEL" \
        --beam-size 5 \
        --entropy-thold 2.4 \
        -mc 0 \
        --output-srt \
        --output-file "/tmp/whisper_${filename}_you" \
        "$tmp_you" 2>/dev/null | whisper_progress "${YOU_LABEL}" "$dur_you"

    GGML_METAL_PATH_RESOURCES="$WHISPER_METAL_RESOURCES" \
    "$WHISPER_BIN" \
        --model "$WHISPER_MODEL" \
        --beam-size 5 \
        --entropy-thold 2.4 \
        -mc 0 \
        --output-srt \
        --output-file "/tmp/whisper_${filename}_caller" \
        "$tmp_caller" 2>/dev/null | whisper_progress "${CALLER_LABEL}" "$dur_caller"

    # Merge SRTs into labeled transcript
    if [ -f "$tmp_srt_you" ] && [ -f "$tmp_srt_caller" ]; then
        echo "  → Merging transcripts..."
        result=$(merge_srt "$tmp_srt_you" "$tmp_srt_caller" "$YOU_LABEL" "$CALLER_LABEL" "$txt_out")
        echo "  ✓ Done ($result)"

        # Relabel Caller lines from a caption-derived speaker timeline, if present.
        speakers_json="$INPUT_DIR/speakers.json"
        if [ -f "$speakers_json" ]; then
            echo "  → Relabeling Caller lines from speakers.json..."
            REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
            ( cd "$REPO_DIR" && python3 -c "
import sys, json
from casting_call.timeline import Span
from casting_call.roster import load_roster
from casting_call.transcript import parse_transcript, resolve_attribution, render_transcript
from casting_call.coverage import coverage_report
txt, spath = sys.argv[1], sys.argv[2]
roster = load_roster('speakers_roster.json')
spans = [Span(**s) for s in json.load(open(spath))]
entries = parse_transcript(open(txt).read())
out, info = resolve_attribution(entries, spans, 1.5, roster.self_name, 'Caller')
open(txt, 'w').write(render_transcript(out))
if info['mode'] == 'collapse':
    print(f\"  ✓ 2-person call: all Caller lines -> {info['speaker']}\")
r = coverage_report(out, 'Caller')
print(f\"  ✓ {r['attributed_lines']}/{r['caller_lines']} Caller lines attributed ({r['attributed_pct']}%)\")
" "$txt_out" "$speakers_json" )
        fi
    else
        echo "  ✗ Transcription failed (one or both channels missing)"
    fi

    rm -f "$tmp_you" "$tmp_caller" "$tmp_srt_you" "$tmp_srt_caller"
done

echo ""
echo "Complete!"
echo "  Audio       → $INPUT_DIR/audio_only/"
echo "  Transcripts → $INPUT_DIR/transcripts/"