"""
marker_track — turn Track 3 into timestamped marker presses.

Track 3 is 99% silence with a handful of short pre-canned clips in it. Handing
that whole track to whisper does not work: on the 2026-08-28 eng-ama call it
merged 18 presses spread over 31 minutes into ONE segment stamped at the first
sound, so every timestamp after the first was lost. Timestamps are the entire
value of the marker rig, so that failure is total even though the words came
through fine.

The fix is to stop asking whisper where the presses are. We already know: the
track is silence by construction, so ffmpeg's silencedetect finds every burst to
the second. Whisper's only job is saying WHICH clip a burst was, on a two-second
slice where there is nothing to merge it with.

    detect_bursts()  -> ffmpeg, exact press times
    transcribe_burst -> whisper, one short slice, one phrase
    bursts_to_markers-> markers.match_marker over the results

Usage:
    python3 -m casting_call.marker_track <markers.wav> <main_transcript.txt>
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from casting_call.markers import embed_markers, match_marker
from casting_call.transcript import parse_transcript, render_transcript

# Anything above this counts as a clip rather than room tone. The track is
# digital silence between clips, so this is not a close call.
NOISE_DB = -40

# A gap shorter than this is inside one clip, not between two. The clips are
# ~1.5s of continuous speech, so short inter-word dips must not split them.
MIN_SILENCE = 0.4

# Slack around each burst when slicing. silencedetect reports the loud middle of
# a clip and trims ~0.3s of quiet attack and decay off each end, so a burst reads
# as ~0.7s where the source clip is ~1.0-1.3s. Padding both ends by this much
# puts the whole word back. There is nothing else on Track 3 to bleed in, and the
# closest two presses on a real call were 2s apart, so erring wide is free.
PAD = 0.4

_SIL_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SIL_END = re.compile(r"silence_end:\s*([0-9.]+)")


def track_duration(path):
    """Track length in seconds, or 0.0 if ffprobe cannot say."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "csv=p=0", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True)
    except (FileNotFoundError, OSError):
        return 0.0
    try:
        return float(proc.stdout.decode().strip())
    except ValueError:
        return 0.0


def parse_silence_log(text, duration):
    """Invert ffmpeg's silence spans into the burst spans between them.

    silencedetect reports where silence starts and ends; the sound is the gaps.
    Handles a track that opens or closes with sound (no bounding silence line)
    by falling back to 0 and `duration`.
    """
    starts = [float(m) for m in _SIL_START.findall(text)]
    ends = [float(m) for m in _SIL_END.findall(text)]

    # Burst starts: track start (unless silence starts at 0), then each silence_end.
    opens = list(ends)
    if not starts or starts[0] > 0:
        opens.insert(0, 0.0)

    bursts = []
    for open_at in opens:
        closes = [s for s in starts if s > open_at]
        close_at = min(closes) if closes else duration
        if close_at > open_at:
            bursts.append((open_at, close_at))
    return bursts


def detect_bursts(audio_path, noise_db=NOISE_DB, min_silence=MIN_SILENCE):
    """Every non-silent span in the track, as (start, end) seconds.

    Returns [] rather than raising when ffmpeg is missing or the file is
    unreadable, so a broken marker track never aborts a whole transcription run.
    """
    cmd = ["ffmpeg", "-i", str(audio_path),
           "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
           "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True)
    except (FileNotFoundError, OSError):
        return []
    return parse_silence_log(proc.stderr.decode(errors="replace"),
                             track_duration(audio_path))


def transcribe_burst(audio_path, start, end, whisper_bin, model, pad=PAD,
                     metal_resources=None):
    """Cut one burst out and transcribe just that slice. '' on any failure.

    A slice holds one clip and nothing else, which is what stops whisper from
    merging presses together the way it does on the full track.

    The slice goes through a temp file rather than a pipe on purpose: whisper-cli
    will read wav bytes from stdin with `-f -`, but then reads the same `-` as
    `--output-file -` and prints nothing to stdout, so piping silently yields an
    empty transcription for every burst.
    """
    lo = max(0.0, start - pad)
    cut = ["ffmpeg", "-v", "error", "-ss", str(lo), "-to", str(end + pad),
           "-i", str(audio_path), "-ar", "16000", "-ac", "1",
           "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"]
    env = dict(os.environ)
    if metal_resources:
        env["GGML_METAL_PATH_RESOURCES"] = metal_resources
    try:
        sliced = subprocess.run(cut, capture_output=True)
        if sliced.returncode != 0 or not sliced.stdout:
            return ""
        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            tmp.write(sliced.stdout)
            tmp.flush()
            proc = subprocess.run(
                [whisper_bin, "-m", str(model), "-f", tmp.name, "-nt", "-np"],
                capture_output=True, env=env,
            )
    except (FileNotFoundError, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.decode(errors="replace").strip()


def bursts_to_markers(bursts, texts):
    """Pair each burst with its transcription and keep the ones that match.

    The burst's own start second is the marker time, floored to match the
    transcript's whole-second lines. A burst whose text matches no marker
    keyword is dropped rather than guessed at: stray noise on Track 3 should
    produce nothing, not a wrong marker.
    """
    out = []
    for (start, _end), text in zip(bursts, texts):
        mtype = match_marker(text)
        if mtype:
            out.append({"t": int(start), "type": mtype, "text": mtype})
    return out


def markers_from_track(audio_path, whisper_bin, model, metal_resources=None,
                       progress=None):
    """Full Track 3 -> markers pass. Returns [{'t','type','text'}] in time order."""
    bursts = detect_bursts(audio_path)
    texts = []
    for n, (start, end) in enumerate(bursts, 1):
        if progress:
            progress(n, len(bursts), start)
        texts.append(transcribe_burst(audio_path, start, end, whisper_bin, model,
                                      metal_resources=metal_resources))
    return bursts_to_markers(bursts, texts)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Detect Track 3 marker presses and fold them into a transcript.")
    ap.add_argument("marker_audio", help="the Track 3 audio (wav/mp3)")
    ap.add_argument("transcript", help="merged transcript to fold markers into")
    ap.add_argument("--whisper-bin", default="whisper-cli")
    ap.add_argument("--model", required=True)
    ap.add_argument("--metal-resources", default=None)
    ap.add_argument("--parts-out", default=None,
                    help="write one line per detected burst here, for reference")
    args = ap.parse_args(argv)

    def tick(n, total, start):
        print(f"  · burst {n}/{total} at {int(start)//60}:{int(start)%60:02d}",
              file=sys.stderr)

    bursts = detect_bursts(args.marker_audio)
    texts = []
    for n, (start, end) in enumerate(bursts, 1):
        tick(n, len(bursts), start)
        texts.append(transcribe_burst(args.marker_audio, start, end,
                                      args.whisper_bin, args.model,
                                      metal_resources=args.metal_resources))
    markers = bursts_to_markers(bursts, texts)

    if args.parts_out:
        # One line per burst, including ones that matched nothing, so a press that
        # failed to recognize is visible rather than just absent.
        Path(args.parts_out).write_text("".join(
            f"[{int(s)//3600:01d}:{int(s)//60%60:02d}:{int(s)%60:02d}] [T3] {t or '(unrecognized)'}\n"
            for (s, _e), t in zip(bursts, texts)
        ), encoding="utf-8")

    if not markers:
        print("no markers recognized on Track 3")
        return 0

    path = Path(args.transcript)
    entries = parse_transcript(path.read_text(encoding="utf-8"))
    path.write_text(render_transcript(embed_markers(entries, markers)), encoding="utf-8")

    counts = {}
    for m in markers:
        counts[m["type"]] = counts.get(m["type"], 0) + 1
    print(f"embedded {len(markers)} marker(s): "
          + ", ".join(f"{n} {t}" for t, n in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
