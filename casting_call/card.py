"""
card — copy and read the Zoom PodTrak P4next's own recordings.

The P4next writes every channel to its microSD card with no Mac software in the
path, which makes the card the backup for OBS audio capture. One press of REC
gives one take:

    <yymmdd_hhmmss>.wav                                     stereo mix (card root)
    P4next_Multitrack/<take>/<take>_MIC1.wav                mic, mono
    P4next_Multitrack/<take>/<take>_USB.wav                 computer audio, stereo
    P4next_Multitrack/<take>/<take>_SOUND_PAD.wav           pads, stereo
    P4next_Multitrack/<take>/<take>_MIC2.wav .. _MIC4.wav   unused inputs

Files over 2 GB continue in `_02`, `_03`... parts. Each file carries a `bext`
chunk (date, start time, time reference) and a `cue ` chunk with one point per
PLAY-key press, as a sample offset. Both are read here with the standard library.

ripa copies the matching take into <name>/card/ (flat), and everything after
that reads only from there, so the card can go back in the recorder.

Usage:
    python3 -m casting_call.card copy <day_dir> [--volume /Volumes/P4NEXT_SD]
    python3 -m casting_call.card input-args <name_dir> <role>
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

VOLUME = "/Volumes/P4NEXT_SD"
MULTITRACK_DIR = "P4next_Multitrack"

ROLES = ("mix", "mic", "usb", "pads")
_SUFFIX_ROLE = {"": "mix", "MIC1": "mic", "USB": "usb", "SOUND_PAD": "pads"}
_IGNORED_SUFFIXES = {"MIC2", "MIC3", "MIC4"}

_NAME_RE = re.compile(
    r"^(?P<take>\d{6}_\d{6})(?:_(?P<suffix>MIC\d|USB|SOUND_PAD))?(?:_(?P<part>\d{2}))?\.wav$",
    re.IGNORECASE)

# OBS names recordings "YYYY-MM-DD HH-MM-SS.mkv".
_OBS_NAME_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _](\d{2})-(\d{2})-(\d{2})")

# The P4next clock is set by hand and was 23 s behind the Mac on 2026-11-14, so
# a take that starts this far before a recording still belongs to it.
CLOCK_SLACK = 120.0


# ── RIFF chunks ──────────────────────────────────────────────────────────────

@dataclass
class WavInfo:
    rate: int = 0
    channels: int = 0
    bits: int = 0
    frames: int = 0
    origin: dt.datetime | None = None   # bext origination date + time
    time_reference: int | None = None   # bext, samples since midnight
    cues: list = field(default_factory=list)  # sample offsets, sorted

    @property
    def duration(self):
        return self.frames / self.rate if self.rate else 0.0

    @property
    def cue_seconds(self):
        return [c / self.rate for c in self.cues] if self.rate else []


def parse_bext(data):
    """(origin datetime or None, time reference in samples) from a bext body.

    Layout (EBU Tech 3285): Description 256, Originator 32, OriginatorReference
    32, OriginationDate 10, OriginationTime 8, TimeReference low/high uint32.
    """
    if len(data) < 346:
        return None, None
    date = data[320:330].decode("ascii", "replace").strip("\0 ")
    time = data[330:338].decode("ascii", "replace").strip("\0 ")
    lo, hi = struct.unpack("<II", data[338:346])
    origin = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H-%M-%S"):
        try:
            origin = dt.datetime.strptime(f"{date} {time}", fmt)
            break
        except ValueError:
            continue
    return origin, lo | (hi << 32)


def parse_cue(data):
    """Sample offsets of every cue point, sorted.

    Each 24-byte point: id, position, data chunk id, chunk start, block start,
    sample offset. The P4next writes the press in both position and sample
    offset; sample offset is the field the spec defines for a data chunk.
    """
    if len(data) < 4:
        return []
    (count,) = struct.unpack("<I", data[:4])
    out = []
    for i in range(count):
        point = data[4 + 24 * i: 28 + 24 * i]
        if len(point) < 24:
            break
        _id, position, _cid, _cs, _bs, offset = struct.unpack("<II4sIII", point)
        out.append(offset or position)
    return sorted(out)


def read_wav_info(path):
    """Format, length, bext and cue facts from a WAV, reading headers only.

    Raises ValueError on a file that is not a RIFF/WAVE.
    """
    info = WavInfo()
    data_size = 0
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError(f"not a WAV file: {path}")
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            cid, size = struct.unpack("<4sI", hdr)
            if cid == b"data":
                data_size = size
                f.seek(size + (size & 1), os.SEEK_CUR)
                continue
            if cid in (b"fmt ", b"bext", b"cue "):
                body = f.read(size)
                if size & 1:
                    f.read(1)
                if cid == b"fmt " and len(body) >= 16:
                    _tag, info.channels, info.rate, _br, _align, info.bits = \
                        struct.unpack("<HHIIHH", body[:16])
                elif cid == b"bext":
                    info.origin, info.time_reference = parse_bext(body)
                elif cid == b"cue ":
                    info.cues = parse_cue(body)
            else:
                f.seek(size + (size & 1), os.SEEK_CUR)
    if info.channels and info.bits:
        info.frames = data_size // (info.channels * info.bits // 8)
    return info


# ── takes ────────────────────────────────────────────────────────────────────

@dataclass
class CardTake:
    take: str                                    # "261114_220657"
    roles: dict = field(default_factory=dict)    # role -> [Path], part order
    ignored: list = field(default_factory=list)  # MIC2..MIC4
    unknown: list = field(default_factory=list)

    def first(self, role=None):
        """First file of a role, or of any role (mix first) when role is None."""
        for r in ([role] if role else ROLES):
            if self.roles.get(r):
                return self.roles[r][0]
        return None


def classify(name):
    """(take, role or 'ignored', part number) for a card file name, else None."""
    m = _NAME_RE.match(name)
    if not m:
        return None
    suffix = (m["suffix"] or "").upper()
    role = _SUFFIX_ROLE.get(suffix)
    if role is None:
        role = "ignored" if suffix in _IGNORED_SUFFIXES else None
    if role is None:
        return None
    return m["take"], role, int(m["part"] or 1)


def find_card_take(name_dir):
    """The card take in <name_dir>/card/, or None if there is none.

    Files that are not part of a take are listed as unknown and left alone.
    When the folder somehow holds two takes, the one with the most files wins.
    """
    card_dir = Path(name_dir) / "card"
    if not card_dir.is_dir():
        return None
    takes, unknown = {}, []
    for p in sorted(card_dir.iterdir()):
        if p.name.startswith(".") or not p.is_file():
            continue
        c = classify(p.name)
        if c is None:
            unknown.append(p)
            continue
        take, role, part = c
        takes.setdefault(take, []).append((role, part, p))
    if not takes:
        return None
    best = max(takes, key=lambda t: len(takes[t]))
    out = CardTake(take=best, unknown=unknown)
    for t, files in takes.items():
        if t != best:
            out.unknown.extend(p for _r, _n, p in files)
    for role, part, p in sorted(takes[best], key=lambda x: (x[0], x[1])):
        if role == "ignored":
            out.ignored.append(p)
        else:
            out.roles.setdefault(role, []).append(p)
    return out


def take_start(take_or_path):
    """Wall-clock start of a take from its bext chunk, else from its file name."""
    path = take_or_path.first() if isinstance(take_or_path, CardTake) else Path(take_or_path)
    if path is None:
        return None
    try:
        info = read_wav_info(path)
        if info.origin:
            return info.origin
    except (OSError, ValueError):
        pass
    c = classify(path.name)
    if c:
        try:
            return dt.datetime.strptime(c[0], "%y%m%d_%H%M%S")
        except ValueError:
            return None
    return None


def obs_start(video_path):
    """Wall-clock start of an OBS recording from its file name, or None."""
    m = _OBS_NAME_RE.search(Path(video_path).stem)
    if not m:
        return None
    try:
        return dt.datetime(*(int(g) for g in m.groups()))
    except ValueError:
        return None


def concat_list(paths, list_path):
    """Write an ffmpeg concat-demuxer list for split parts. Returns list_path."""
    lines = []
    for p in paths:
        quoted = str(Path(p).resolve()).replace("'", "'\\''")
        lines.append(f"file '{quoted}'\n")
    Path(list_path).write_text("".join(lines), encoding="utf-8")
    return list_path


def input_args(paths, list_path):
    """ffmpeg input arguments for one role: the file, or a concat of its parts."""
    if not paths:
        return []
    if len(paths) == 1:
        return ["-i", str(paths[0])]
    return ["-f", "concat", "-safe", "0", "-i", str(concat_list(paths, list_path))]


def join_parts(paths, out_path):
    """Join `_02` style parts into one WAV with the concat demuxer (no re-encode).

    A single part is returned as is. Raises RuntimeError if ffmpeg fails.
    """
    if len(paths) == 1:
        return Path(paths[0])
    out_path = Path(out_path)
    list_path = out_path.with_suffix(".concat.txt")
    cmd = ["ffmpeg", "-v", "error", "-y", *input_args(paths, list_path),
           "-c", "copy", str(out_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True)
    except (FileNotFoundError, OSError) as e:
        raise RuntimeError(f"ffmpeg not runnable: {e}") from e
    finally:
        if list_path.exists():
            list_path.unlink()
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="replace").strip() or "ffmpeg failed")
    return out_path


# ── copying off the card ─────────────────────────────────────────────────────

def card_takes(volume):
    """{take: [Path, ...]} for every take on a mounted card."""
    volume = Path(volume)
    takes = {}
    candidates = list(volume.glob("*.wav")) + list(volume.glob("*.WAV"))
    multi = volume / MULTITRACK_DIR
    if multi.is_dir():
        for d in multi.iterdir():
            if d.is_dir():
                candidates += [p for p in d.iterdir() if p.suffix.lower() == ".wav"]
    for p in candidates:
        if p.name.startswith("."):
            continue
        c = classify(p.name)
        if c:
            takes.setdefault(c[0], []).append(p)
    return takes


def _probe_duration(path):
    try:
        proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                               "-of", "csv=p=0", str(path)], capture_output=True)
        return float(proc.stdout.decode().strip())
    except (FileNotFoundError, OSError, ValueError):
        return 0.0


def match_takes(recordings, takes, slack=CLOCK_SLACK):
    """Pair takes with recordings by start time.

    recordings: [(name, start datetime, duration s)], takes: {take: start datetime}.
    A take belongs to the recording whose span, widened by `slack` at the front,
    holds its start; the nearest start wins a tie. Returns ({take: name}, [unmatched]).
    """
    matched, left = {}, []
    for take, start in sorted(takes.items()):
        best, best_gap = None, None
        for name, rstart, dur in recordings:
            if rstart is None or start is None:
                continue
            delta = (start - rstart).total_seconds()
            if -slack <= delta <= max(dur, 0.0):
                if best_gap is None or abs(delta) < best_gap:
                    best, best_gap = name, abs(delta)
        if best:
            matched[take] = best
        else:
            left.append(take)
    return matched, left


def copy_takes(day_dir, volume=VOLUME, out=print):
    """Copy every card take that belongs to a recording in day_dir into <name>/card/.

    Never deletes anything from the card. Already-copied files of the same size
    are skipped. Returns {take: name}.
    """
    day_dir, volume = Path(day_dir), Path(volume)
    if not volume.is_dir():
        return {}
    videos = sorted(list(day_dir.glob("*.mkv")) + list(day_dir.glob("*.mp4")))
    recordings = [(v.stem, obs_start(v), _probe_duration(v)) for v in videos]
    takes = card_takes(volume)
    starts = {}
    for take, files in takes.items():
        mix = [p for p in files if classify(p.name)[1] == "mix"]
        starts[take] = take_start((mix or files)[0])
    matched, left = match_takes(recordings, starts)
    for take, name in sorted(matched.items()):
        dest = day_dir / name / "card"
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            out(f"card: cannot create {dest}: {e}")
            continue
        copied = 0
        for src in sorted(takes[take]):
            target = dest / src.name
            try:
                if target.exists() and target.stat().st_size == src.stat().st_size:
                    continue
                shutil.copy2(src, target)
                copied += 1
            except OSError as e:
                out(f"card: failed to copy {src.name}: {e}")
        out(f"card: take {take} -> {name}/card/ ({copied} file(s) copied, "
            f"{len(takes[take]) - copied} already there)")
    if left:
        out(f"card: left on the card, no matching recording here: {', '.join(sorted(left))}")
    return matched


def main(argv=None):
    ap = argparse.ArgumentParser(description="Copy and read P4next card takes.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("copy", help="copy matching takes off a mounted card")
    c.add_argument("day_dir")
    c.add_argument("--volume", default=os.environ.get("CARD_VOLUME", VOLUME))
    i = sub.add_parser("input-args", help="ffmpeg input args for one role, one per line")
    i.add_argument("name_dir")
    i.add_argument("role", choices=ROLES)
    args = ap.parse_args(argv)

    if args.cmd == "copy":
        copy_takes(args.day_dir, args.volume)
        return 0
    take = find_card_take(args.name_dir)
    if take is None or not take.roles.get(args.role):
        return 1
    list_path = Path(args.name_dir) / "card" / f".{args.role}.concat.txt"
    print("\n".join(input_args(take.roles[args.role], list_path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
