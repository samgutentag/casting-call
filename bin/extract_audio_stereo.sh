#!/bin/bash

# Transcribe OBS multi-track recordings with speaker labels.
#
# The rig records the screen plus TWO separate audio tracks into one .mkv:
#   Track 1 (stream 0:a:0) = your mic (Elgato Wave)
#   Track 2 (stream 0:a:1) = the far side (Chrome / Meet, via App Audio Capture)
#
# The .mkv is the canonical recording and is read directly (no remux needed for
# transcription). For a playable video with you=left / caller=right, render one
# separately with convert_video.sh (ripv).
#
# NOTE: filename kept as extract_audio_stereo.sh so the `ripa` alias keeps working.
#       The pipeline is no longer stereo/pan-split. Rename to transcribe_multitrack.sh
#       and repoint the alias when convenient. (See docs/superpowers/specs 2026-07-24.)
#
# Flow per recording:
#   extract each track -> detect silence -> transcribe (skip a dead track) ->
#   weave by timestamp -> strip silence/junk hallucinations -> relabel if a
#   speakers.json timeline exists.
#
# Usage: extract_audio_stereo.sh [directory] [whisper_model_path]
# Output: transcripts/<name>.txt
#
# Dependencies: brew install whisper-cpp ffmpeg

INPUT_DIR="${1:-.}"
WHISPER_MODEL="${2:-$HOME/whisper-models/ggml-large-v3.bin}"

YOU_LABEL="You"
CALLER_LABEL="Caller"

# Silence detection. SILENCE_DBFS is the noise floor. Spans at/above
# SILENCE_CLEAN_MIN seconds are used to strip whisper hallucinations (it invents
# speech on dead audio). A single span at/above SILENCE_GAP_SECONDS is treated as
# a probable mid-call capture dropout and warned about.
SILENCE_DBFS=-50
SILENCE_CLEAN_MIN=15
SILENCE_GAP_SECONDS=300

WHISPER_BIN="whisper-cli"
WHISPER_METAL_RESOURCES="$(brew --prefix whisper-cpp)/share/whisper-cpp"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Check dependencies
if ! command -v "$WHISPER_BIN" &>/dev/null; then
    echo "Error: $WHISPER_BIN not found. Run: brew install whisper-cpp"; exit 1
fi
if ! command -v ffmpeg &>/dev/null; then
    echo "Error: ffmpeg not found. Run: brew install ffmpeg"; exit 1
fi
if ! command -v ffprobe &>/dev/null; then
    echo "Error: ffprobe not found (should come with ffmpeg)"; exit 1
fi
if [ ! -f "$WHISPER_MODEL" ]; then
    echo "Error: Whisper model not found at: $WHISPER_MODEL"
    echo "  mkdir -p ~/whisper-models && curl -o ~/whisper-models/ggml-large-v3.bin -L \\"
    echo "    'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin?download=true'"
    exit 1
fi

mkdir -p "$INPUT_DIR/transcripts"

shopt -s nullglob
recordings=("$INPUT_DIR"/*.mkv "$INPUT_DIR"/*.mp4)
shopt -u nullglob

# ── helpers ──────────────────────────────────────────────────────────────────
get_duration() {
    ffprobe -v error -show_entries format=duration -of csv=p=0 "$1" 2>/dev/null | cut -d. -f1
}

has_stream() {
    ffprobe -v error -select_streams "$2" -show_entries stream=index \
        -of csv=p=0 "$1" 2>/dev/null | grep -q .
}

# All silent spans >= min_dur, as "start:end,start:end" (empty if none).
# Args: file, floor_db, min_dur.
silent_spans() {
    ffmpeg -hide_banner -nostats -i "$1" -af "silencedetect=noise=$2dB:d=$3" \
        -f null /dev/null 2>&1 | awk '
            /silence_start:/    { s=$NF }
            /silence_duration:/ { d=$NF; printf "%s%s:%.3f", sep, s, s+d; sep="," }'
}

# Longest span "start dur" from a spans string (empty if none).
longest_span() {
    awk -v spans="$1" 'BEGIN{
        n=split(spans, a, ","); mdur=-1; mstart=0
        for(i=1;i<=n;i++){ if(a[i]=="")continue; split(a[i], p, ":");
            d=p[2]-p[1]; if(d>mdur){mdur=d; mstart=p[1]} }
        if(mdur>=0) printf "%s %s", mstart, mdur
    }'
}

# Fraction (0..1) of duration covered by silence.
silent_fraction() {
    awk -v spans="$1" -v dur="$2" 'BEGIN{
        n=split(spans, a, ","); tot=0
        for(i=1;i<=n;i++){ if(a[i]=="")continue; split(a[i], p, ":"); tot+=p[2]-p[1] }
        print (dur>0)? tot/dur : 0
    }'
}

fmt_hms() { awk -v s="$1" 'BEGIN{ s=int(s); printf "%d:%02d:%02d", s/3600, (s%3600)/60, s%60 }'; }

PROGRESS_SCRIPT=$(mktemp /tmp/whisper_progress_XXXX.py)
trap 'rm -f "$PROGRESS_SCRIPT"' EXIT
cat > "$PROGRESS_SCRIPT" <<'PYEOF'
import sys, re
label = sys.argv[1]; duration = float(sys.argv[2]); bar_width = int(sys.argv[3])
def parse_time(t):
    p = t.replace(',', '.').split(':')
    if len(p) == 3: return int(p[0])*3600 + int(p[1])*60 + float(p[2])
    if len(p) == 2: return int(p[0])*60 + float(p[1])
    return 0
def format_time(s):
    m, s = divmod(int(s), 60); h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"
pattern = re.compile(r'\[\s*[\d:.,]+\s*-->\s*([\d:.,]+)\s*\]')
last_pct = -1
for line in sys.stdin:
    match = pattern.search(line)
    if not match: continue
    current = parse_time(match.group(1)); pct = min(current / duration, 1.0)
    int_pct = int(pct * 100)
    if int_pct == last_pct: continue
    last_pct = int_pct
    filled = int(bar_width * pct)
    bar = '█' * filled + '░' * (bar_width - filled)
    sys.stdout.write(f"\r  → {label} [{bar}] {int_pct}% ({format_time(current)}/{format_time(duration)})")
    sys.stdout.flush()
bar = '█' * bar_width
sys.stdout.write(f"\r  → {label} [{bar}] 100% ({format_time(duration)}/{format_time(duration)})\n")
sys.stdout.flush()
PYEOF

whisper_progress() {
    local label="$1" duration="$2" bar_width=30
    if [ -z "$duration" ] || [ "$duration" -eq 0 ] 2>/dev/null; then
        local spin=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏') i=0
        while IFS= read -r line; do
            printf "\r  → %s [%s] transcribing..." "$label" "${spin[$((i % 10))]}"; i=$((i + 1))
        done
        printf "\r  → %s ✓%-40s\n" "$label" ""; return
    fi
    python3 "$PROGRESS_SCRIPT" "$label" "$duration" "$bar_width"
}

merge_srt() {
    python3 - "$1" "$2" "$3" "$4" "$5" <<'EOF'
import sys, re
def parse_srt(path, label):
    entries = []
    try:
        content = open(path).read()
    except FileNotFoundError:
        return entries
    for block in content.strip().split('\n\n'):
        lines = block.strip().splitlines()
        if len(lines) < 3: continue
        m = re.match(r'(\d+:\d+:\d+,\d+)\s-->', lines[1])
        if not m: continue
        ts = m.group(1).replace(',', '.'); h, mn, s = ts.split(':')
        secs = int(h)*3600 + int(mn)*60 + float(s)
        text = ' '.join(lines[2:]).strip()
        if text: entries.append((secs, ts.replace('.', ','), label, text))
    return entries
srt_you, srt_caller, label_you, label_caller, out_file = sys.argv[1:]
entries = parse_srt(srt_you, label_you) + parse_srt(srt_caller, label_caller)
entries.sort(key=lambda x: x[0])
with open(out_file, 'w') as f:
    for _, ts, label, text in entries:
        f.write(f"[{ts.split(',')[0]}] [{label}] {text}\n")
print(f"Merged {len(entries)} segments")
EOF
}

# Transcribe one track wav -> srt, unless it is essentially all silence (skip to
# save time and to avoid whisper hallucinating on dead audio; an empty srt lets
# the merge proceed with just the other track).
transcribe_track() {
    local wav="$1" srt_base="$2" label="$3" spans="$4"
    local dur frac
    dur=$(get_duration "$wav")
    frac=$(silent_fraction "$spans" "${dur:-0}")
    if awk -v f="$frac" 'BEGIN{exit !(f >= 0.95)}'; then
        echo "  ⏭  ${label} track is essentially silent, skipping transcription"
        : > "${srt_base}.srt"
        return
    fi
    GGML_METAL_PATH_RESOURCES="$WHISPER_METAL_RESOURCES" \
    "$WHISPER_BIN" --model "$WHISPER_MODEL" --beam-size 5 --entropy-thold 2.4 -mc 0 \
        --output-srt --output-file "$srt_base" "$wav" 2>/dev/null \
        | whisper_progress "$label" "$(get_duration "$wav")"
}

# ── run ──────────────────────────────────────────────────────────────────────
total=${#recordings[@]}
echo "Found $total recording(s) (.mkv/.mp4)"
echo "Model: $WHISPER_MODEL"
echo "Tracks: 0:a:0=[${YOU_LABEL}]  0:a:1=[${CALLER_LABEL}]"
echo ""
if [ "$total" -eq 0 ]; then
    echo "Nothing to do. Point this at a folder of OBS .mkv recordings."; exit 0
fi

current=0
for input_file in "${recordings[@]}"; do
    filename=$(basename "$input_file"); filename="${filename%.*}"
    current=$((current + 1))
    txt_out="$INPUT_DIR/transcripts/${filename}.txt"
    tmp_you="/tmp/whisper_${filename}_you.wav"
    tmp_caller="/tmp/whisper_${filename}_caller.wav"
    tmp_srt_you="/tmp/whisper_${filename}_you.srt"
    tmp_srt_caller="/tmp/whisper_${filename}_caller.srt"

    echo "[$current/$total] $filename"

    if [ -f "$txt_out" ]; then
        echo "  ⏭  Transcript already exists, skipping"; continue
    fi
    if ! has_stream "$input_file" "a:1"; then
        echo "  ✗ Only one audio track — not a multi-track recording."
        echo "    If the far side is on video only, recover from captions: ripcap $input_file"
        continue
    fi

    # Extract each track (mono 16k + loudnorm) straight from the source container.
    echo "  → Extracting tracks..."
    ffmpeg -i "$input_file" -map 0:a:0 -af "loudnorm=I=-16:TP=-1.5:LRA=11" \
        -ar 16000 -ac 1 -c:a pcm_s16le "$tmp_you" -y -loglevel error
    ffmpeg -i "$input_file" -map 0:a:1 -af "loudnorm=I=-16:TP=-1.5:LRA=11" \
        -ar 16000 -ac 1 -c:a pcm_s16le "$tmp_caller" -y -loglevel error

    # Detect silence BEFORE transcribing: spans clean hallucinations, and a
    # sustained one flags a probable dropout.
    spans_you=$(silent_spans "$tmp_you" "$SILENCE_DBFS" "$SILENCE_CLEAN_MIN")
    spans_caller=$(silent_spans "$tmp_caller" "$SILENCE_DBFS" "$SILENCE_CLEAN_MIN")

    read -r gap_start gap_dur <<< "$(longest_span "$spans_caller")"
    if [ -n "$gap_dur" ] && awk -v d="$gap_dur" -v t="$SILENCE_GAP_SECONDS" 'BEGIN{exit !(d>=t)}'; then
        echo "  ⚠  Far side (${CALLER_LABEL}) went silent for $(fmt_hms "$gap_dur") starting at $(fmt_hms "$gap_start")."
        echo "     If the call was still going, the capture dropped. Recover from captions:"
        echo "       ripcap $input_file --region <x,y,w,h>"
    fi

    # Transcribe each track (dead tracks are skipped inside transcribe_track).
    transcribe_track "$tmp_you" "/tmp/whisper_${filename}_you" "$YOU_LABEL" "$spans_you"
    transcribe_track "$tmp_caller" "/tmp/whisper_${filename}_caller" "$CALLER_LABEL" "$spans_caller"

    # Weave.
    echo "  → Merging transcripts..."
    result=$(merge_srt "$tmp_srt_you" "$tmp_srt_caller" "$YOU_LABEL" "$CALLER_LABEL" "$txt_out")
    echo "  ✓ Woven ($result)"

    # Strip hallucinations: lines inside a silent span for their channel, + junk phrases.
    ( cd "$REPO_DIR" && python3 -m casting_call.clean "$txt_out" \
        --you-label "$YOU_LABEL" --caller-label "$CALLER_LABEL" \
        --silent-you "$spans_you" --silent-caller "$spans_caller" \
        | sed 's/^/  → /' )

    # Track 3 (0:a:2): pre-canned marker phrases, if the rig recorded them. Transcribe
    # that track (do NOT silence-skip it: it is mostly silence with short phrases) and
    # fold recognized markers into the transcript inline. Non-marker whisper noise on
    # the track is ignored, since only known marker keywords match.
    if has_stream "$input_file" "a:2"; then
        echo "  → Parsing Track 3 markers..."
        tmp_t3="/tmp/whisper_${filename}_t3.wav"
        tmp_t3_srt="/tmp/whisper_${filename}_t3.srt"
        tmp_t3_txt="/tmp/whisper_${filename}_t3.txt"
        ffmpeg -i "$input_file" -map 0:a:2 -ar 16000 -ac 1 -c:a pcm_s16le "$tmp_t3" -y -loglevel error
        GGML_METAL_PATH_RESOURCES="$WHISPER_METAL_RESOURCES" \
        "$WHISPER_BIN" --model "$WHISPER_MODEL" -mc 0 --output-srt \
            --output-file "/tmp/whisper_${filename}_t3" "$tmp_t3" 2>/dev/null \
            | whisper_progress "markers" "$(get_duration "$tmp_t3")"
        merge_srt "$tmp_t3_srt" "/dev/null" "T3" "-" "$tmp_t3_txt" >/dev/null
        ( cd "$REPO_DIR" && python3 -m casting_call.markers "$txt_out" "$tmp_t3_txt" | sed 's/^/  → /' )
        rm -f "$tmp_t3" "$tmp_t3_srt" "$tmp_t3_txt"
    fi

    # Relabel Caller lines from a caption-derived speaker timeline, if present.
    speakers_json="$INPUT_DIR/speakers.json"
    if [ -f "$speakers_json" ]; then
        echo "  → Relabeling Caller lines from speakers.json..."
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

    rm -f "$tmp_you" "$tmp_caller" "$tmp_srt_you" "$tmp_srt_caller"
done

echo ""
echo "Complete!  Transcripts → $INPUT_DIR/transcripts/"
