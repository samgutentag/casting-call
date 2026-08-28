"""
digest — build a per-call marker digest from a merged transcript.

Reads a casting-call stereo transcript (with `[MARKER]` lines already folded in
by `casting_call.markers`) and emits a structured JSON summary of the call: the
conversation cut into sections at each `topic` press, every marker press and
spoken aside filed into the section it fell in, a rollup of everything
actionable across section boundaries, and the ~30s of conversation around each
item. `video` markers also carry a screenshot seeked from the recording.

Two channels feed the same item list: Stream Deck presses (`markers`) and the
phrases Sam speaks on his own track (`asides`). They carry a `source` so the
page can label them, but nothing downstream has to branch on it.

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

from casting_call.asides import asides_from_entries
from casting_call.markers import MARK_LABEL, MARKER_TYPES
from casting_call.transcript import parse_transcript

# The marker type that opens a section rather than sitting inside one.
TOPIC_TYPE = "topic"

# How close a spoken aside has to be to a press to count as the same intent.
# Sam's habit is press the key, then say the task into the muted mic, so the
# aside trails the press by a few seconds. Wide enough to catch that, narrow
# enough that two unrelated tasks in the same minute stay separate.
PAIR_WINDOW = 45

# Which marker types a spoken aside can pair with. Only actions: a press says
# who owns the task, the spoken line says what it is, and together they are one
# item. A spoken note pairs with nothing, because a note is its own thing.
PAIRABLE = {"action": ("action-me", "action-them", "action")}

# Which item types roll up into which bucket at the top of the page.
ROLLUP_BUCKETS = {
    "yours": ("action-me", "action"),      # 'action' is the unowned fallback
    "theirs": ("action-them",),
    "questions": ("question",),
}


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


def section_by_topic(items, topic_presses, call_start, call_end):
    """Cut the call into sections at each `topic` press and file items into them.

    Returns a list of {'start','end','implicit','topic_marker','items'}. A press
    opens a section and is never listed inside its own section, since it is the
    heading rather than a moment in it.

    Two cases the callers actually hit:
      - no topic presses at all (every transcript recorded before the topic key
        existed): one implicit section spanning the call, so the page renders
        like an ordinary digest instead of collapsing to nothing.
      - conversation before the first press: a leading section, dropped only when
        the call and the first press start together. It is kept even when it
        holds no items, because the talking still happened and a page that
        starts at the first press reads as though the call did too.
    """
    bounds = []
    if not topic_presses:
        bounds.append({"start": call_start, "end": None,
                       "implicit": True, "topic_marker": None})
    else:
        first = topic_presses[0]["t"]
        if call_start < first:
            bounds.append({"start": call_start, "end": first,
                           "implicit": True, "topic_marker": None})
        for n, press in enumerate(topic_presses):
            nxt = topic_presses[n + 1]["t"] if n + 1 < len(topic_presses) else None
            bounds.append({"start": press["t"], "end": nxt, "implicit": False,
                           "topic_marker": press})

    sections = []
    for b in bounds:
        lo, hi = b["start"], b["end"]
        inside = [
            i for i in items
            if i["t"] >= lo and (hi is None or i["t"] < hi)
            and not (i["source"] == "marker" and i["type"] == TOPIC_TYPE)
        ]
        sections.append(dict(b, tstr=format_ts(lo),
                             endstr=format_ts(hi) if hi is not None else format_ts(call_end),
                             items=sorted(inside, key=lambda i: i["t"])))
    return sections


def pair_asides(markers, asides, window=PAIR_WINDOW):
    """Fold each spoken aside into the press it restates, when there is one.

    Returns (paired_markers, leftover_asides). A press keeps its own type and
    timestamp (it is the one that knows the owner, and it is the more precise
    moment) and gains the aside's words. Without this the rollup counts one task
    twice whenever Sam both pressed the key and said it out loud, which is his
    normal habit rather than an edge case.

    Each press absorbs at most one aside and each aside is used at most once, so
    two tasks spoken back to back after a single press stay two items.
    """
    taken = set()
    for m in markers:
        if m["type"] not in sum(PAIRABLE.values(), ()):
            continue
        best, best_gap = None, None
        for n, a in enumerate(asides):
            if n in taken or m["type"] not in PAIRABLE.get(a["type"], ()):
                continue
            gap = abs(a["t"] - m["t"])
            if gap <= window and (best_gap is None or gap < best_gap):
                best, best_gap = n, gap
        if best is not None:
            taken.add(best)
            m["text"] = asides[best]["text"]
            m["paired"] = True
    return markers, [a for n, a in enumerate(asides) if n not in taken]


def rollup(items):
    """Everything actionable, flattened across section boundaries.

    Sam reads this before re-reading the call, so a spoken "action item" and a
    pressed Action For Me land in the same bucket: same intent, two channels.
    The unowned `action` fallback gets its own bucket rather than being guessed
    into one of the other two.
    """
    out = {name: [] for name in ROLLUP_BUCKETS}
    out["unowned"] = []
    for i in items:
        if i["type"] == "action" and i["source"] == "marker":
            out["unowned"].append(i)
            continue
        for name, types in ROLLUP_BUCKETS.items():
            if i["type"] in types:
                out[name].append(i)
                break
    return out


def build_digest_from_entries(entries, window_seconds=30, video_path=None):
    """Core grouping, no filesystem read. Returns the digest dict without the
    name/paths (those are added by build_digest)."""
    speech, markers = split_markers(entries)

    by_type = {}
    items = []
    for m in markers:
        moment = {
            "type": m["type"],
            "t": m["t"],
            "tstr": format_ts(m["t"]),
            "source": "marker",
            "text": None,
            "paired": False,
            "context": window_for(m, speech, window_seconds),
            "screenshot": None,
        }
        if m["type"] == "video" and video_path:
            moment["screenshot"] = frame_data_uri(video_path, m["t"])
        by_type.setdefault(m["type"], []).append(moment)
        items.append(moment)

    # Spoken flags off the [You] track. Same shape as a press so sectioning and
    # the rollup never have to care which channel an item arrived on.
    asides = asides_from_entries(entries)
    _, unpaired = pair_asides(items, asides)
    for a in unpaired:
        items.append({
            "type": a["type"],
            "t": a["t"],
            "tstr": format_ts(a["t"]),
            "source": "aside",
            "text": a["text"],
            "phrase": a["phrase"],
            "paired": False,
            "context": window_for(a, speech, window_seconds),
            "screenshot": None,
        })
    items.sort(key=lambda i: (i["t"], 0 if i["source"] == "marker" else 1))

    call_start = min((e["t"] for e in entries), default=0)
    call_end = max((e["t"] for e in entries), default=0)
    topic_presses = [m for m in items
                     if m["source"] == "marker" and m["type"] == TOPIC_TYPE]

    # Canonical MARKER_TYPES order first; anything unrecognized trails, sorted.
    ordered = [t for t in MARKER_TYPES if t in by_type]
    extras = sorted(t for t in by_type if t not in MARKER_TYPES)
    groups = [
        {"type": t, "count": len(by_type[t]), "markers": by_type[t]}
        for t in ordered + extras
    ]
    return {
        "window_seconds": window_seconds,
        "sections": section_by_topic(items, topic_presses, call_start, call_end),
        "rollup": rollup(items),
        "groups": groups,
        "counts": {t: len(v) for t, v in by_type.items()},
        "total": len(markers),
        "aside_total": len(asides),
        "aside_paired": len(asides) - len(unpaired),
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
