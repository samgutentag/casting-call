#!/bin/bash
# callcheck - verify a stereo call recording is capturing BOTH sides.
# Convention (matches the ripa pipeline): left = you (mic), right = them (call/system audio).
#
# Usage:
#   callcheck <file>                  analyze an existing recording (mp3/mov/wav/m4a/...)
#   callcheck --live [-d DEV] [-t N]  capture N sec (default 6) from input DEV, then analyze
#   callcheck --devices               list avfoundation audio input devices
#
# DEV is a device index (e.g. 2) or a name substring (e.g. "Recording Calls"); default "Recording Calls".
# Exit 0 if both channels look live, 1 if either looks dead, 2 on usage/setup error.

set -u

# Judge on MEAN, not max: a dead channel can still show a transient max spike
# (the Corey far-end maxed at -24.9 from crosstalk but its MEAN was -71, pure
# noise floor). A real channel with anyone talking averages around -25 to -45.
# -55 dB cleanly separates "captured a conversation" from "silence". Make sure
# both sides actually produce audio during a --live check or a real side reads low.
NOISE_FLOOR_DB=-55

die() { echo "callcheck: $*" >&2; exit 2; }
command -v ffmpeg >/dev/null || die "ffmpeg not found (brew install ffmpeg)"

# Only the audio-devices section of avfoundation's listing.
audio_devices() {
  ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 \
    | awk '/AVFoundation audio devices/{a=1;next} /AVFoundation video devices/{a=0} a'
}

list_devices() {
  echo "Audio input devices (use the index or a name substring with -d):"
  audio_devices
}

# index passthrough, or resolve a name substring to its avfoundation index
resolve_device() {
  [[ "$1" =~ ^[0-9]+$ ]] && { echo "$1"; return; }
  audio_devices | grep -i "$1" | sed -E 's/.*\[([0-9]+)\].*/\1/' | head -1
}

# echo "mean max" (dB) for one channel of a file; pan picks the channel (c0 or c1).
# -nostdin: ffmpeg otherwise consumes the script's stdin and corrupts its output
# when called inside process substitution.
chan_stats() {
  ffmpeg -nostdin -hide_banner -i "$1" -af "pan=mono|$2,volumedetect" -f null /dev/null 2>&1 \
    | awk '/mean_volume/{m=$5} /max_volume/{x=$5} END{print m, x}'
}

analyze() {
  local f="$1" rc=0 side pan who mean max verdict
  echo "Checking: $f"
  for entry in "L:c0=c0:you " "R:c0=c1:them"; do
    side="${entry%%:*}"; pan="${entry#*:}"; who="${pan#*:}"; pan="${pan%%:*}"
    read -r mean max < <(chan_stats "$f" "$pan")
    if [ -z "${max:-}" ]; then echo "  $side: (no second channel / mono file)"; continue; fi
    if [[ -z "$mean" || "$mean" == *inf* ]]; then
      verdict="DEAD"; rc=1
    elif awk "BEGIN{exit !($mean < $NOISE_FLOOR_DB)}"; then
      verdict="DEAD"; rc=1
    else
      verdict="OK"
    fi
    printf "  %s (%s): max %7s dB   mean %8s dB   -> %s\n" "$side" "$who" "${max:-n/a}" "${mean:-n/a}" "$verdict"
  done
  if [ $rc -eq 0 ]; then echo "  PASS - both channels capturing."; else echo "  FAIL - a channel looks dead (see above). Fix before you rely on this recording."; fi
  return $rc
}

case "${1:-}" in
  ""|-h|--help) sed -n '2,12p' "$0"; echo; list_devices; exit 0 ;;
  -l|--devices) list_devices; exit 0 ;;
  --live)
    shift; dev="Recording Calls"; secs=6
    while [ $# -gt 0 ]; do
      case "$1" in -d) dev="${2:-}"; shift 2;; -t) secs="${2:-}"; shift 2;; *) shift;; esac
    done
    idx="$(resolve_device "$dev")"
    [ -z "$idx" ] && { echo "Couldn't find an audio input matching '$dev'." >&2; list_devices; exit 2; }
    tmp="$(mktemp).wav"
    echo "Capturing ${secs}s from device [$idx] (\"$dev\")..."
    echo ">>> TALK now for the left/you channel, and make sure the other side has audio playing for right/them <<<"
    ffmpeg -hide_banner -f avfoundation -i ":$idx" -t "$secs" -ac 2 "$tmp" -y -loglevel error \
      || die "capture failed - is device [$idx] a 2-channel input?"
    analyze "$tmp"; rc=$?
    rm -f "$tmp"; exit $rc ;;
  *)
    [ -f "$1" ] || die "no such file: $1"
    analyze "$1"; exit $? ;;
esac
