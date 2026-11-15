"""
frames — save one full-resolution screen grab per marker press.

Each press on the marker track gets a PNG of the screen at that exact moment,
written next to the transcript as <rec_dir>/markers/marker-HHMMSS.png. No
scaling: the point is to read code, slides and small UI text off the grab later,
long after `ripo` has trashed the multi-gigabyte .mkv.

`ripa` runs this right after it finds the presses, so the frames exist before
the overnight render deletes the source. A manifest (markers.json) maps each
press to its frame so the process-transcript skill can find them without
guessing file names.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def format_ts(seconds):
    """Seconds -> 'h:mm:ss', the shape transcript lines use."""
    s = int(seconds)
    return f"{s // 3600:01d}:{s // 60 % 60:02d}:{s % 60:02d}"


def frame_name(seconds, taken):
    """marker-HHMMSS.png, with -2, -3... when two presses share a second.

    `taken` is the set of names already handed out for this recording; the
    chosen name is added to it.
    """
    s = int(seconds)
    base = f"marker-{s // 3600:02d}{s // 60 % 60:02d}{s % 60:02d}"
    name, n = f"{base}.png", 2
    while name in taken:
        name, n = f"{base}-{n}.png", n + 1
    taken.add(name)
    return name


def frame_cmd(video_path, seconds, out_path):
    """ffmpeg command for one native-resolution PNG at `seconds`.

    -ss before -i seeks to the nearest keyframe and then decodes forward to the
    exact time, which is both fast on a long 4K file and frame accurate. No -vf,
    so the grab keeps the recording's full resolution.
    """
    return ["ffmpeg", "-v", "error", "-ss", f"{seconds:.3f}", "-i", str(video_path),
            "-map", "0:v:0", "-frames:v", "1", "-y", str(out_path)]


def grab_frames(video_path, markers, frames_dir, run=subprocess.run):
    """Write one PNG per marker. Returns a frame path (or None) per marker.

    Uses each marker's exact press time ('at') when present, falling back to its
    whole second ('t'). A failed grab yields None instead of raising, so one bad
    seek never costs the transcript its markers.
    """
    frames_dir = Path(frames_dir)
    try:
        frames_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return [None] * len(markers)
    taken, out = set(), []
    for m in markers:
        at = float(m.get("at", m["t"]))
        path = frames_dir / frame_name(at, taken)
        try:
            proc = run(frame_cmd(video_path, at, path), capture_output=True)
            ok = proc.returncode == 0 and path.exists() and path.stat().st_size > 0
        except (FileNotFoundError, OSError):
            ok = False
        out.append(str(path) if ok else None)
    return out


def write_manifest(json_path, video_path, markers, frames):
    """markers.json: every press with its time, type and frame path."""
    rows = [{
        "t": m["t"],
        "at": round(float(m.get("at", m["t"])), 3),
        "tstr": format_ts(m["t"]),
        "type": m["type"],
        "frame": f,
    } for m, f in zip(markers, frames)]
    Path(json_path).write_text(json.dumps(
        {"video": str(video_path) if video_path else None, "markers": rows},
        indent=2) + "\n", encoding="utf-8")
    return rows


def load_manifest(json_path):
    """{whole second: frame path} from a markers.json, or {} if absent/broken."""
    try:
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for row in data.get("markers", []):
        if row.get("frame"):
            out.setdefault(int(row["t"]), row["frame"])
    return out
