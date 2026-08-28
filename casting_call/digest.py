"""
digest — build a per-call marker digest from a merged transcript.

Reads a casting-call stereo transcript (with `[MARKER]` lines already folded in
by `casting_call.markers`) and emits a structured JSON summary: every Stream
Deck marker press grouped by type, with the ~30s of conversation around it and,
for `video` markers, a single screenshot seeked from the recording.

The JSON is consumed by the `marker-digest` skill, which adds the model-written
call summary and per-moment one-liners and renders the self-contained HTML.
Everything here is deterministic and testable; nothing here calls a model.

Usage:
    python3 -m casting_call.digest <merged_transcript.txt> [--window 30] [--no-frames]
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

from casting_call.markers import MARK_LABEL, MARKER_TYPES
from casting_call.transcript import parse_transcript


def format_ts(seconds):
    """Seconds -> 'h:mm:ss', the same shape transcript lines use."""
    h, rem = divmod(seconds, 3600)
    mn, sec = divmod(rem, 60)
    return f"{h:01d}:{mn:02d}:{sec:02d}"


def split_markers(entries):
    """Partition parsed entries into (speech, markers).

    Marker entries (label == MARK_LABEL) become {'t', 'type'} where type is the
    keyword the button spoke ('flag', 'video', ...). Everything else is speech.
    """
    speech, markers = [], []
    for e in entries:
        if e["label"] == MARK_LABEL:
            markers.append({"t": e["t"], "type": e["text"].strip().lower()})
        else:
            speech.append(e)
    return speech, markers


def window_for(marker, speech, window_seconds):
    """Speech lines within +/- window_seconds of the marker (bounds inclusive).

    `speech` is already marker-free (see split_markers) and time-ordered, so the
    result is the conversation around the press. Each line gets a 'tstr' added.
    """
    lo, hi = marker["t"] - window_seconds, marker["t"] + window_seconds
    return [dict(e, tstr=format_ts(e["t"])) for e in speech if lo <= e["t"] <= hi]


def frame_data_uri(video_path, t):
    """Seek one frame at `t` seconds and return it as a JPEG data: URI.

    Returns None on any failure (no ffmpeg, missing/unreadable video, seek past
    the end) so a single bad frame never aborts the whole digest.
    """
    if not video_path:
        return None
    cmd = [
        "ffmpeg", "-ss", str(t), "-i", str(video_path),
        "-frames:v", "1", "-vf", "scale='min(960,iw)':-2",
        "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
        "-loglevel", "error",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True)
    except (FileNotFoundError, OSError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(proc.stdout).decode()


def _find_sibling(dirs, stem, exts):
    """First existing <dir>/<stem><ext>, searching dirs then exts in order."""
    for d in dirs:
        for ext in exts:
            cand = d / f"{stem}{ext}"
            if cand.exists():
                return str(cand)
    return None


def resolve_artifacts(transcript_path):
    """Locate the call's audio (.mp3) and video (.mp4, then .mkv) by stem.

    Searches the transcript's own dir and its parent, covering both the flat
    layout and `<name>/transcripts/<name>.txt`. Returns (audio, video), each a
    path string or None.
    """
    p = Path(transcript_path)
    dirs = [p.parent, p.parent.parent]
    audio = _find_sibling(dirs, p.stem, [".mp3"])
    video = _find_sibling(dirs, p.stem, [".mp4", ".mkv"])
    return audio, video


def build_digest_from_entries(entries, window_seconds=30, video_path=None):
    """Core grouping, no filesystem read. Returns the digest dict without the
    name/paths (those are added by build_digest)."""
    speech, markers = split_markers(entries)

    by_type = {}
    for m in markers:
        moment = {
            "type": m["type"],
            "t": m["t"],
            "tstr": format_ts(m["t"]),
            "context": window_for(m, speech, window_seconds),
            "screenshot": None,
        }
        if m["type"] == "video" and video_path:
            moment["screenshot"] = frame_data_uri(video_path, m["t"])
        by_type.setdefault(m["type"], []).append(moment)

    # Canonical MARKER_TYPES order first; anything unrecognized trails, sorted.
    ordered = [t for t in MARKER_TYPES if t in by_type]
    extras = sorted(t for t in by_type if t not in MARKER_TYPES)
    groups = [
        {"type": t, "count": len(by_type[t]), "markers": by_type[t]}
        for t in ordered + extras
    ]
    return {
        "window_seconds": window_seconds,
        "groups": groups,
        "counts": {t: len(v) for t, v in by_type.items()},
        "total": len(markers),
    }


def build_digest(transcript_path, window_seconds=30, frames=True):
    """Read a transcript file and return the full digest dict."""
    p = Path(transcript_path)
    entries = parse_transcript(p.read_text(encoding="utf-8"))
    audio, video = resolve_artifacts(p)
    d = build_digest_from_entries(
        entries, window_seconds, video_path=video if frames else None
    )
    d["name"] = p.stem
    d["transcript_path"] = str(p)
    d["audio_path"] = audio
    d["video_path"] = video
    return d


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(
        description="Build a marker digest (JSON) from a merged transcript.",
    )
    parser.add_argument("transcript", help="merged stereo transcript .txt")
    parser.add_argument("--window", type=int, default=30,
                        help="context window in seconds around each marker")
    parser.add_argument("--no-frames", action="store_true",
                        help="skip screenshot extraction for video markers")
    args = parser.parse_args(argv)

    p = Path(args.transcript)
    if not p.is_file():
        print(f"digest: no such transcript: {p}", file=sys.stderr)
        return 2

    d = build_digest(p, window_seconds=args.window, frames=not args.no_frames)
    json.dump(d, sys.stdout, indent=2)
    sys.stdout.write("\n")
    if d["total"] == 0:
        print("digest: no [MARKER] lines found", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
