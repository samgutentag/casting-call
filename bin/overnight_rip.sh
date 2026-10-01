#!/bin/bash

# Overnight cleanup for the raw OBS recordings under a call_logs tree.
#
# For every .mkv under the root:
#   1. ripa that one recording, only if <name>/<name>.mp3 or .txt is missing.
#      ripa transcribes every recording in the directory it is given and
#      overwrites each transcript, so it runs in a staging directory holding a
#      symlink to just this .mkv (plus the day's speakers.json, if any), and the
#      output is copied back into <name>/. Other recordings on that day, done or
#      not, are left alone.
#   2. ripv its day directory if <name>/<name>.mp4 is missing (ripv skips
#      anything already rendered on its own).
#   3. Move the .mkv to the Trash once mp3, txt, and a full-length mp4 exist.
#      "Full-length" means the mp4 duration is within 2s of the mkv. ripv writes
#      straight to the final path, so a killed run leaves a truncated mp4 that
#      ripv would skip forever; those get trashed instead so the next run redoes them.
#
# Ends with the space reclaimed: mkv bytes trashed minus mp4 bytes created.
# Nothing is freed on disk until the Trash is emptied.
#
# Usage: overnight_rip.sh [root]        (default: ~/Desktop/call_logs)
#        DRY_RUN=1 overnight_rip.sh     (report the plan, change nothing)

ROOT="${1:-$HOME/Desktop/call_logs}"
BIN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RIPA="$BIN/extract_audio_stereo.sh"
RIPV="$BIN/convert_video.sh"
DRY_RUN="${DRY_RUN:-0}"
LOG="$ROOT/overnight-rip-$(date +%Y%m%d-%H%M%S).log"

if [ ! -d "$ROOT" ]; then
    echo "Error: directory not found: $ROOT"; exit 1
fi
ROOT="$(cd "$ROOT" && pwd)"
for tool in ffprobe trash; do
    command -v "$tool" &>/dev/null || { echo "Error: $tool not found"; exit 1; }
done

exec > >(tee -a "$LOG") 2>&1

# Keep the Mac awake for as long as this script runs.
caffeinate -dims -w $$ &

duration() {
    ffprobe -v error -show_entries format=duration -of csv=p=0 "$1" 2>/dev/null
}

bytes() {
    stat -f %z "$1" 2>/dev/null || echo 0
}

human() {
    awk -v b="$1" 'BEGIN{
        split("B KB MB GB TB", u, " "); i=1; s=(b<0)?-1:1; b*=s
        while (b>=1024 && i<5) { b/=1024; i++ }
        printf "%s%.2f %s", (s<0)?"-":"", b, u[i] }'
}

same_length() {  # $1 mkv, $2 mp4
    local a b
    a=$(duration "$1"); b=$(duration "$2")
    [ -n "$a" ] && [ -n "$b" ] && awk -v a="$a" -v b="$b" 'BEGIN{d=a-b; if(d<0)d=-d; exit !(d<=2)}'
}

mkvs=()
while IFS= read -r -d '' f; do mkvs+=("$f"); done \
    < <(find "$ROOT" -name '*.mkv' -not -path '*/.Trash/*' -print0 | sort -z)

echo "overnight_rip: ${#mkvs[@]} mkv file(s) under $ROOT"
echo "log: $LOG"
[ "$DRY_RUN" = 1 ] && echo "DRY RUN: nothing will be ripped, rendered, or trashed"
echo "--------------------------"

# Pass 1: work out which recordings need ripa, which day directories need ripv, and which mp4s
# already existed so the savings only count ones rendered tonight.
# (bash 3.2 on macOS: no associative arrays, so these are newline lists.)
ripa_recs=""; ripv_dirs=""; had_mp4=""
add_line() {  # $1 list variable name, $2 value to append once
    local cur
    eval "cur=\"\$$1\""
    case $'\n'"$cur" in *$'\n'"$2"$'\n'*) return ;; esac
    eval "$1=\"\$cur\$2\"\$'\\n'"
}
for mkv in "${mkvs[@]}"; do
    dir=$(dirname "$mkv"); name=$(basename "$mkv" .mkv); rec="$dir/$name"
    if [ ! -s "$rec/$name.mp3" ] || [ ! -s "$rec/$name.txt" ]; then
        add_line ripa_recs "$mkv"
    fi
    if [ -f "$rec/$name.mp4" ] && ! same_length "$mkv" "$rec/$name.mp4"; then
        echo "truncated mp4 from an earlier run, trashing so ripv redoes it: $rec/$name.mp4"
        [ "$DRY_RUN" = 1 ] || trash "$rec/$name.mp4"
        add_line ripv_dirs "$dir"
    elif [ -f "$rec/$name.mp4" ]; then
        add_line had_mp4 "$mkv"
    else
        add_line ripv_dirs "$dir"
    fi
done

count() { [ -z "$1" ] && echo 0 || printf '%s' "$1" | grep -c ''; }
echo "ripa needed for $(count "$ripa_recs") recording(s), ripv needed in $(count "$ripv_dirs") day dir(s)"

if [ "$DRY_RUN" != 1 ]; then
    while IFS= read -r mkv; do
        [ -n "$mkv" ] || continue
        dir=$(dirname "$mkv"); name=$(basename "$mkv" .mkv)
        echo ""; echo "== ripa: $mkv"
        stage=$(mktemp -d "${TMPDIR:-/tmp}/overnight-ripa.XXXXXX") || {
            echo "  ✗ could not create a staging directory; skipping"; continue; }
        ln -s "$mkv" "$stage/$name.mkv"
        [ -f "$dir/speakers.json" ] && ln -s "$dir/speakers.json" "$stage/speakers.json"
        if bash "$RIPA" "$stage" < /dev/null && [ -d "$stage/$name" ]; then
            mkdir -p "$dir/$name" && cp -R "$stage/$name/." "$dir/$name/" \
                || echo "  ✗ could not copy ripa output back to $dir/$name"
        else
            echo "  ✗ ripa failed for $name"
        fi
        rm -rf "$stage"
    done <<< "$ripa_recs"
    while IFS= read -r dir; do
        [ -n "$dir" ] || continue
        echo ""; echo "== ripv: $dir"
        bash "$RIPV" "$dir" < /dev/null || echo "  ✗ ripv exited non-zero for $dir"
    done <<< "$ripv_dirs"
fi

# Pass 2: verify and trash.
echo ""; echo "--------------------------"
trashed=0; kept=0; mkv_bytes=0; mp4_bytes=0
for mkv in "${mkvs[@]}"; do
    dir=$(dirname "$mkv"); name=$(basename "$mkv" .mkv); rec="$dir/$name"
    missing=()
    [ -s "$rec/$name.mp3" ] || missing+=(mp3)
    [ -s "$rec/$name.txt" ] || missing+=(txt)
    if [ ! -f "$rec/$name.mp4" ]; then
        missing+=(mp4)
    elif ! same_length "$mkv" "$rec/$name.mp4"; then
        missing+=("full-length mp4")
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        echo "KEEP   $name  (missing: ${missing[*]})"
        kept=$((kept + 1)); continue
    fi

    size=$(bytes "$mkv")
    if [ "$DRY_RUN" = 1 ]; then
        echo "WOULD  $name  ($(human "$size"))"
    elif trash "$mkv"; then
        echo "TRASH  $name  ($(human "$size"))"
    else
        echo "KEEP   $name  (trash failed)"
        kept=$((kept + 1)); continue
    fi
    trashed=$((trashed + 1))
    mkv_bytes=$((mkv_bytes + size))
    case $'\n'"$had_mp4" in *$'\n'"$mkv"$'\n'*) ;; *) mp4_bytes=$((mp4_bytes + $(bytes "$rec/$name.mp4"))) ;; esac
done

echo ""
echo "Trashed $trashed mkv, kept $kept"
echo "mkv moved to Trash:  $(human "$mkv_bytes")"
echo "new mp4 written:     $(human "$mp4_bytes")"
echo "Space saved:         $(human $((mkv_bytes - mp4_bytes)))  (freed once the Trash is emptied)"
