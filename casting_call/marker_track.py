"""
marker_track — turn the marker track into timestamped marker presses.

The marker track (Track 4 on the four-track rig, Track 3 before 2026-10-08)
is 99% silence with a handful of short pre-canned clips in it. Handing
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

Two rigs, one track. Since 2026-11-14 the P4next's pad A plays a single clip
that says "marker", so every burst is a press of the same type and whisper has
nothing to decide. choose_mode() picks between the two rigs:

    auto (default): whisper the first PROBE_BURSTS bursts. Any hit on one of the
      seven Stream Deck phrases (markers.LEGACY_TYPES) means an old recording,
      so the rest go through whisper as before. No legacy hit, including when
      whisper is missing or hears nothing, means the pad rig: every burst
      becomes one `marker` press and whisper is not called again.
    single / legacy: force one or the other (MARKER_MODE in ripa).

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

from casting_call import frames as frame_grab
from casting_call.markers import LEGACY_TYPES, SINGLE_TYPE, embed_markers, match_marker
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
# puts the whole word back. There is nothing else on the marker track to bleed in, and the
# closest two presses on a real call were 2s apart, so erring wide is free.
PAD = 0.4

# Pad rig: a burst shorter than this is a click or a USB pop, not the 0.74s
# "marker" clip (which silencedetect trims to roughly 0.4-0.6s).
MIN_PRESS = 0.15

# Pad rig: a press starting this soon after the previous one is the same
# moment pressed twice, not a second marker. MIN_SILENCE already joins presses
# whose clips overlap or sit under 0.4s apart into one burst; this catches the
# double tap that lands just after the clip ends.
MERGE_WITHIN = 1.5

# How many bursts auto mode hands whisper before deciding which rig recorded it.
PROBE_BURSTS = 3

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
    keyword is dropped rather than guessed at: stray noise on the marker track should
    produce nothing, not a wrong marker.
    """
    out = []
    for (start, _end), text in zip(bursts, texts):
        mtype = match_marker(text or "")
        if mtype:
            out.append({"t": int(start), "at": start, "type": mtype, "text": mtype})
    return out


def choose_mode(probe_texts):
    """'legacy' if any probe burst says one of the Stream Deck phrases, else 'single'."""
    for text in probe_texts:
        if match_marker(text or "") in LEGACY_TYPES:
            return "legacy"
    return "single"


def presses_from_bursts(bursts, min_press=MIN_PRESS, merge_within=MERGE_WITHIN):
    """Pad rig: one `marker` press per burst, keeping the exact start time.

    Drops clicks shorter than min_press and folds a press that starts within
    merge_within seconds of the last kept one into it.
    """
    out, last = [], None
    for start, end in bursts:
        if end - start < min_press:
            continue
        if last is not None and start - last < merge_within:
            continue
        out.append({"t": int(start), "at": start, "type": SINGLE_TYPE,
                    "text": SINGLE_TYPE})
        last = start
    return out


def markers_from_bursts(bursts, transcribe, mode="auto", progress=None):
    """Bursts -> (markers, mode used, per-burst whisper texts).

    `transcribe(start, end)` returns whisper's text for one burst. A burst
    whisper never heard (pad rig, past the probe) has text None.
    """
    texts = [None] * len(bursts)

    def run(i):
        if progress:
            progress(i + 1, len(bursts), bursts[i][0])
        texts[i] = transcribe(*bursts[i])

    if mode == "auto":
        for i in range(min(PROBE_BURSTS, len(bursts))):
            run(i)
        mode = choose_mode(texts)
    if mode == "single":
        return presses_from_bursts(bursts), mode, texts
    for i in range(len(bursts)):
        if texts[i] is None:
            run(i)
    return bursts_to_markers(bursts, texts), mode, texts


def markers_from_track(audio_path, whisper_bin, model, metal_resources=None,
                       progress=None, mode="auto"):
    """Full marker track -> markers pass. Returns [{'t','at','type','text'}] in time order."""
    markers, _mode, _texts = markers_from_bursts(
        detect_bursts(audio_path),
        lambda s, e: transcribe_burst(audio_path, s, e, whisper_bin, model,
                                      metal_resources=metal_resources),
        mode=mode, progress=progress)
    return markers


def merge_cue_marks(markers, cue_times, within=MERGE_WITHIN):
    """Add the card's PLAY-key marks as `marker` presses, in time order.

    A PLAY mark within `within` seconds of a pad press is the same moment
    marked twice (the habit is pad A, and PLAY is the fallback), so it is
    dropped rather than doubled.
    """
    out = list(markers)
    for t in sorted(cue_times):
        if any(abs(float(m.get("at", m["t"])) - t) < within for m in out):
            continue
        out.append({"t": int(t), "at": t, "type": SINGLE_TYPE, "text": SINGLE_TYPE})
    out.sort(key=lambda m: float(m.get("at", m["t"])))
    return out


def parse_times(text):
    """'12.5,300.25' -> [12.5, 300.25]. Blank or junk entries are skipped."""
    out = []
    for part in (text or "").split(","):
        try:
            out.append(float(part))
        except ValueError:
            continue
    return out


format_ts = frame_grab.format_ts


def parts_label(start, end, text):
    """What markers.txt says about one burst."""
    if text is None:
        return f"burst {end - start:.2f}s"
    return text or "(unrecognized)"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Detect marker-track presses and fold them into a transcript.")
    ap.add_argument("marker_audio", help="the marker track audio (wav/mp3)")
    ap.add_argument("transcript", help="merged transcript to fold markers into")
    ap.add_argument("--whisper-bin", default="whisper-cli")
    ap.add_argument("--model", required=True)
    ap.add_argument("--metal-resources", default=None)
    ap.add_argument("--parts-out", default=None,
                    help="write one line per detected burst here, for reference")
    ap.add_argument("--mode", choices=("auto", "single", "legacy"), default="auto",
                    help="single = P4next pad clip, legacy = the seven Stream Deck "
                         "clips, auto = decide from the first few bursts")
    ap.add_argument("--video", default=None,
                    help="recording to take one full-resolution frame from per press")
    ap.add_argument("--frames-dir", default=None,
                    help="where marker-HHMMSS.png frames go (needs --video)")
    ap.add_argument("--json-out", default=None,
                    help="write markers.json (time, type, frame per press) here")
    ap.add_argument("--extra-marks", default="",
                    help="more press times on the video timeline, 't,t,...' "
                         "(the card's PLAY-key marks)")
    args = ap.parse_args(argv)

    def tick(n, total, start):
        print(f"  · burst {n}/{total} at {int(start)//60}:{int(start)%60:02d}",
              file=sys.stderr)

    bursts = detect_bursts(args.marker_audio)
    markers, mode, texts = markers_from_bursts(
        bursts,
        lambda s, e: transcribe_burst(args.marker_audio, s, e,
                                      args.whisper_bin, args.model,
                                      metal_resources=args.metal_resources),
        mode=args.mode, progress=tick)
    if bursts:
        rig = "P4next pad clip" if mode == "single" else "Stream Deck clips"
        print(f"marker track read as the {rig} ({len(bursts)} burst(s))")

    if args.parts_out:
        # One line per burst, including ones that matched nothing, so a press that
        # failed to recognize is visible rather than just absent. Pad-rig bursts
        # whisper never heard are listed by length instead.
        Path(args.parts_out).write_text("".join(
            f"[{format_ts(s)}] [T3] {parts_label(s, e, t)}\n"
            for (s, e), t in zip(bursts, texts)
        ), encoding="utf-8")

    cues = parse_times(args.extra_marks)
    if cues:
        before = len(markers)
        markers = merge_cue_marks(markers, cues)
        added = len(markers) - before
        print(f"card PLAY marks: {len(cues)} found, {added} added, "
              f"{len(cues) - added} already a pad press")

    if not markers:
        print("no markers recognized on the marker track")
        return 0

    frames = [None] * len(markers)
    if args.video and args.frames_dir:
        frames = frame_grab.grab_frames(args.video, markers, args.frames_dir)
        got = sum(1 for f in frames if f)
        print(f"saved {got}/{len(markers)} full-resolution frame(s) to {args.frames_dir}")
    if args.json_out:
        frame_grab.write_manifest(args.json_out, args.video, markers, frames)

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
