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

The card is only mounted long enough for bin/pull_card.sh (pull.py) to copy
new takes into a staging inbox, because in USB File Transfer mode the P4next
stops being the Mac's audio device. Sam then moves the takes that matter next
to their recordings by hand, and ripa reads them where they sit:

    <day>/2026-11-10-corey-sync.mkv
    <day>/2026-11-10-corey-sync.card/      take folder renamed to match (rule 1)
    <day>/261110_140212/                   or left unrenamed (rule 2: paired by
                                           bext start against the .mkv's birth
                                           time, refused past 10 minutes)

ripa never reads the mounted card or the inbox, and never moves or copies a
take; only align.json and the other outputs go into <day>/<name>/.

Usage:
    python3 -m casting_call.card find <name_dir> [--video <mkv>]
    python3 -m casting_call.card input-args <name_dir> <role> [--video <mkv>]
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
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

_TAKE_DIR_RE = re.compile(r"^\d{6}_\d{6}$")

# Rule 2 pairs an unrenamed take with a recording by start time. The P4next
# clock is set by hand (23 s behind the Mac on 2026-11-14); past this gap the
# take is more likely another call than clock drift.
MAX_PAIR_GAP = 600.0


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
    folder: Path | None = None                   # where the take was read from
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


def read_take_dir(card_dir):
    """The take inside one folder, or None if it holds none.

    Files that are not part of a take are listed as unknown and left alone.
    When the folder somehow holds two takes, the one with the most files wins.
    """
    card_dir = Path(card_dir)
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
    out = CardTake(take=best, folder=card_dir, unknown=unknown)
    for t, files in takes.items():
        if t != best:
            out.unknown.extend(p for _r, _n, p in files)
    for role, part, p in sorted(takes[best], key=lambda x: (x[0], x[1])):
        if role == "ignored":
            out.ignored.append(p)
        else:
            out.roles.setdefault(role, []).append(p)
    return out


def video_birth(video_path):
    """When a recording started: the file's birth time (OBS creates it at the
    start, and a same-volume mv keeps it), else the time in an OBS file name."""
    try:
        st = os.stat(video_path)
        born = getattr(st, "st_birthtime", None)
        if born:
            return dt.datetime.fromtimestamp(born)
    except OSError:
        pass
    return obs_start(video_path)


def lookup_card_take(name_dir, video=None, max_gap=MAX_PAIR_GAP):
    """(take or None, note or None) for the recording whose outputs go in name_dir.

    Rule 1: <day>/<name>.card/. Rule 2: the unrenamed <day>/yymmdd_hhmmss/ take
    whose bext start is closest to the video's birth time, within max_gap.
    """
    name_dir = Path(name_dir)
    day, name = name_dir.parent, name_dir.name
    take = read_take_dir(day / f"{name}.card")
    if take:
        return take, f"card take {take.take} from {name}.card/"
    if not video:
        return None, None
    try:
        dirs = [d for d in day.iterdir() if d.is_dir() and _TAKE_DIR_RE.match(d.name)]
    except OSError:
        return None, None
    vstart = video_birth(video)
    if not dirs or vstart is None:
        return None, None
    best = None
    for d in sorted(dirs):
        t = read_take_dir(d)
        start = take_start(t) if t else None
        if start is None:
            continue
        gap = (start - vstart).total_seconds()
        if best is None or abs(gap) < abs(best[1]):
            best = (t, gap)
    if best is None:
        return None, None
    t, gap = best
    when = "before" if gap < 0 else "after"
    if abs(gap) > max_gap:
        return None, (f"nearest unrenamed card take {t.folder.name}/ starts {abs(gap):.0f} s "
                      f"{when} the recording, more than {max_gap / 60:.0f} min: not paired. "
                      f"Rename it to {name}.card/ if it belongs to this call.")
    return t, (f"card take {t.folder.name}/ paired by start time: it starts "
               f"{abs(gap):.0f} s {when} the recording")


def find_card_take(name_dir, video=None):
    """The card take for a recording (see lookup_card_take), or None."""
    return lookup_card_take(name_dir, video)[0]


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


# ── finding takes on the card or in the inbox ────────────────────────────────

def _wavs(d):
    try:
        return [p for p in Path(d).iterdir() if p.is_file() and p.suffix.lower() == ".wav"]
    except OSError:
        return []


def card_takes(root):
    """{take: [Path, ...]} for every take under root.

    Reads both layouts: the card's (mix at the root, the rest in
    P4next_Multitrack/<take>/) and the inbox's (everything in <take>/).
    """
    root = Path(root)
    takes = {}
    candidates = _wavs(root)
    dirs = []
    try:
        dirs = [d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")]
    except OSError:
        pass
    multi = root / MULTITRACK_DIR
    if multi.is_dir():
        dirs += [d for d in multi.iterdir() if d.is_dir()]
    for d in dirs:
        candidates += _wavs(d)
    for p in candidates:
        if p.name.startswith("."):
            continue
        c = classify(p.name)
        if c:
            takes.setdefault(c[0], []).append(p)
    return takes


def main(argv=None):
    ap = argparse.ArgumentParser(description="Find and read P4next card takes.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="say which take belongs to a recording; exit 1 if none")
    f.add_argument("name_dir")
    f.add_argument("--video", default=None)
    i = sub.add_parser("input-args", help="ffmpeg input args for one role, one per line")
    i.add_argument("name_dir")
    i.add_argument("role", choices=ROLES)
    i.add_argument("--video", default=None)
    args = ap.parse_args(argv)

    take, note = lookup_card_take(args.name_dir, args.video)
    if args.cmd == "find":
        if note:
            print(note)
        return 0 if take else 1
    if take is None or not take.roles.get(args.role):
        return 1
    name_dir = Path(args.name_dir)
    try:
        name_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"cannot create {name_dir}: {e}", file=sys.stderr)
        return 1
    print("\n".join(input_args(take.roles[args.role], name_dir / f".{args.role}.concat.txt")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
