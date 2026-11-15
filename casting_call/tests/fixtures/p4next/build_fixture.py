"""
Rebuild the P4next fixture from the 2026-11-14 test call.

    python3 casting_call/tests/fixtures/p4next/build_fixture.py <card_dir> <obs.mkv>

Cuts a 6.4 s window around the first two pad bursts out of the card take
(card time 84.6 s to 91.0 s) and a 7.4 s window out of the OBS file starting at
video time 84.6 s, at 8 kHz mono so the fixture stays small. Both start at the
same number, so the real offset survives the cut: card time = video time +
1.13727 s, with one pad press (video 89.24 s) inside both windows.
Card files keep their real bext and cue facts, moved to the window: the bext
start and time reference shift by 84.6 s, and the PLAY mark keeps its real
position (137.04 s card time), which lands past the end of the cut.
"""

import datetime as dt
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from casting_call.card import read_wav_info  # noqa: E402

HERE = Path(__file__).resolve().parent
CARD_START, LENGTH, OFFSET, RATE = 84.6, 6.4, 1.13727, 8000
TAKE = "261114_220657"


def pcm(src, start, length):
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(length), "-i", str(src),
         "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        capture_output=True, check=True).stdout


def chunk(cid, body):
    return struct.pack("<4sI", cid, len(body)) + body + (b"\0" if len(body) & 1 else b"")


def write_card_wav(dest, data, origin, time_ref, cues):
    bext = bytearray(602)
    bext[256:256 + 11] = b"ZOOM P4next"
    bext[320:330] = origin.strftime("%Y-%m-%d").encode()
    bext[330:338] = origin.strftime("%H:%M:%S").encode()
    bext[338:346] = struct.pack("<II", time_ref & 0xFFFFFFFF, time_ref >> 32)
    fmt = struct.pack("<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16)
    cue = struct.pack("<I", len(cues)) + b"".join(
        struct.pack("<II4sIII", i + 1, c, b"data", 0, 0, c) for i, c in enumerate(cues))
    body = b"WAVE" + chunk(b"bext", bytes(bext)) + chunk(b"fmt ", fmt) + \
        chunk(b"data", data) + chunk(b"cue ", cue)
    dest.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def main(card_dir, mkv):
    card_dir = Path(card_dir)
    out = HERE / "card"
    out.mkdir(exist_ok=True)
    src_info = read_wav_info(card_dir / f"{TAKE}.wav")
    origin = src_info.origin + dt.timedelta(seconds=CARD_START)
    time_ref = int((src_info.time_reference / src_info.rate + CARD_START) * RATE)
    cues = [int(round(c / src_info.rate * RATE - CARD_START * RATE)) for c in src_info.cues]
    for suffix, length in (("", LENGTH), ("_MIC1", LENGTH), ("_USB", LENGTH),
                           ("_SOUND_PAD", LENGTH), ("_MIC2", 0.5)):
        name = f"{TAKE}{suffix}.wav"
        write_card_wav(out / name, pcm(card_dir / name, CARD_START, length),
                       origin, time_ref, cues)
    video_start = CARD_START
    obs_name = dt.datetime(2026, 11, 14, 22, 7, 21) + dt.timedelta(seconds=video_start)
    dest = HERE / f"{obs_name:%Y-%m-%d %H-%M-%S}.mka"
    maps = []
    for i in range(4):
        maps += ["-map", f"0:a:{i}"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{video_start:.5f}", "-t", str(LENGTH + 1),
                    "-i", str(mkv), *maps, "-ac", "1", "-ar", str(RATE),
                    "-c:a", "flac", str(dest)], check=True)
    print(f"wrote {out} and {dest.name}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
