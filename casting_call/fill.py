"""
fill — patch OBS audio dropouts from the P4next card take, before transcription.

ripa calls this right after it splits the .mkv into 16 kHz mono WAVs, and only
when <name>/card/ holds a take. With no take it is never called, so a
recording without a card runs exactly as it did before the card existed.

Steps:
  1. Line the card up with the video (align.py) and write <name>/align.json.
  2. Find each OBS dropout: a span of digital silence (>= DIGITAL_MIN s), or a
     quiet span at or above the caption-backup threshold (DROPOUT_MIN s).
  3. Where the card role has sound over that span (MIC1 for Track 2, USB for
     Track 3), cut the span from the card, shifted onto the OBS timeline, and
     splice it into the track WAV in place. Pad or trim, never stretch.
  4. If OBS Track 4 is digital silence, rebuild it from the card's pad file.
  5. Hand back the card's PLAY-key cue marks on the video timeline.
  6. Print one health line per track and write the transcript header.

Usage (from ripa):
    python3 -m casting_call.fill <name_dir> --video <mkv> --you <wav> \\
        --caller <wav> [--markers <wav>] --report <txt> --header <txt> \\
        --cues-out <txt> --filled-caller-out <txt>
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from casting_call import align, card
from casting_call.frames import format_ts

SILENCE_DB = -50       # ripa's SILENCE_DBFS
DIGITAL_DB = -90       # OBS digital silence reads -91 dB
DIGITAL_MIN = 1.0
DROPOUT_MIN = 60.0     # ripa's CAPTION_GAP_SECONDS
CARD_QUIET_MIN = 0.5
MIN_COVER = 1.0        # seconds of card sound needed to call a span a dropout

# Same loudness target ripa applies to the OBS voice tracks, so a spliced span
# sits at the level of the audio around it.
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"

_SIL_START = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[0-9.]+)")
_MAX_VOL = re.compile(r"max_volume:\s*(-?[0-9.]+|-inf) dB")


# ── measuring ────────────────────────────────────────────────────────────────

def wav_duration(path):
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except (OSError, wave.Error, EOFError):
        return 0.0


def parse_silences(text, duration):
    """(start, end) silent spans from silencedetect output; an open span ends at EOF."""
    starts = [max(0.0, float(x)) for x in _SIL_START.findall(text)]
    ends = [float(x) for x in _SIL_END.findall(text)]
    spans = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration
        if e > s:
            spans.append((s, e))
    return spans


def silences(path, noise_db, min_dur, duration=None):
    """Silent spans of at least min_dur at noise_db. [] if ffmpeg fails."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
           "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True)
    except (FileNotFoundError, OSError):
        return []
    if duration is None:
        duration = wav_duration(path)
    return parse_silences(proc.stderr.decode(errors="replace"), duration)


def peak_db(path):
    """Peak level in dB, -inf for all zeros, None if unreadable."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
           "-af", "volumedetect", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True)
    except (FileNotFoundError, OSError):
        return None
    m = _MAX_VOL.search(proc.stderr.decode(errors="replace"))
    if not m:
        return None
    return float("-inf") if m[1] == "-inf" else float(m[1])


def merge_spans(spans):
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def overlap(spans, a, b):
    """Seconds of `spans` falling inside (a, b)."""
    return sum(max(0.0, min(b, e) - max(a, s)) for s, e in spans)


def dropout_candidates(digital, quiet, min_quiet=DROPOUT_MIN):
    """Union of digital-silence spans and quiet spans at or above min_quiet."""
    return merge_spans(list(digital) + [s for s in quiet if s[1] - s[0] >= min_quiet])


def trim_to_sound(a, b, card_quiet):
    """(a, b) with any card silence at either end cut off.

    A dropout that runs into a stretch where nothing was playing reads as one
    long span on OBS; the card says where the missing sound actually was.
    """
    for s, e in sorted(card_quiet):
        if s <= a < e:
            a = min(e, b)
    for s, e in sorted(card_quiet, reverse=True):
        if s < b <= e:
            b = max(s, a)
    return a, b


def split_by_card(candidates, card_quiet, min_cover=MIN_COVER):
    """(dropouts the card covers, trimmed to its sound; spans where it is silent too)."""
    covered, uncovered = [], []
    for a, b in candidates:
        sound = (b - a) - overlap(card_quiet, a, b)
        if sound >= min_cover:
            covered.append(trim_to_sound(a, b, card_quiet))
        else:
            uncovered.append((a, b))
    return covered, uncovered


# ── cutting and splicing ─────────────────────────────────────────────────────

def aligned_cmd(inputs, offset, duration, out_path, loudnorm=True):
    """ffmpeg command putting a card role on the OBS timeline as 16 kHz mono.

    card time = video time + offset, so a positive offset skips that much of the
    card and a negative one delays it. apad plus -t pads or trims the end to the
    video's length; nothing is time-stretched.
    """
    filters = []
    pre = []
    if offset >= 0:
        pre = ["-ss", f"{offset:.4f}"]
    else:
        filters.append(f"adelay={int(round(-offset * 1000))}:all=1")
    if loudnorm:
        filters.append(LOUDNORM)
    filters.append("apad")
    return ["ffmpeg", "-v", "error", "-y", *pre, *inputs, "-af", ",".join(filters),
            "-t", f"{duration:.3f}", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(out_path)]


def render_aligned(inputs, offset, duration, out_path, loudnorm=True):
    try:
        proc = subprocess.run(aligned_cmd(inputs, offset, duration, out_path, loudnorm),
                              capture_output=True)
    except (FileNotFoundError, OSError) as e:
        raise RuntimeError(f"ffmpeg not runnable: {e}") from e
    if proc.returncode != 0 or not Path(out_path).exists():
        raise RuntimeError(proc.stderr.decode(errors="replace").strip() or "ffmpeg failed")
    return Path(out_path)


def splice(obs_wav, card_wav, spans, block=16000):
    """Replace `spans` of obs_wav with the same spans of card_wav, in place.

    Both must share rate, width and channel count (ripa's 16 kHz mono s16).
    Streams block by block, so an hour-long track never sits in memory.
    """
    obs_wav = Path(obs_wav)
    tmp = obs_wav.with_suffix(".fill.wav")
    with wave.open(str(obs_wav), "rb") as src, wave.open(str(card_wav), "rb") as alt:
        if (src.getframerate(), src.getsampwidth(), src.getnchannels()) != \
                (alt.getframerate(), alt.getsampwidth(), alt.getnchannels()):
            raise ValueError("track and card WAVs differ in format")
        rate, width = src.getframerate(), src.getsampwidth() * src.getnchannels()
        frames = [(int(a * rate), int(b * rate)) for a, b in spans]
        with wave.open(str(tmp), "wb") as dst:
            dst.setparams(src.getparams())
            pos, total = 0, src.getnframes()
            while pos < total:
                n = min(block, total - pos)
                chunk = bytearray(src.readframes(n))
                got = alt.readframes(n)
                for a, b in frames:
                    lo, hi = max(a, pos), min(b, pos + n)
                    if lo < hi:
                        i, j = (lo - pos) * width, (hi - pos) * width
                        piece = got[i:j]
                        chunk[i:j] = piece + bytes((j - i) - len(piece))
                dst.writeframes(bytes(chunk))
                pos += n
    os.replace(tmp, obs_wav)


def cue_marks(take, offset, duration):
    """PLAY-key marks on the video timeline (seconds), from the first file that has them."""
    for role in card.ROLES:
        for path in take.roles.get(role, []):
            try:
                info = card.read_wav_info(path)
            except (OSError, ValueError):
                continue
            if info.cues:
                return [t - offset for t in info.cue_seconds if 0 <= t - offset <= duration]
    return []


def fmt_spans(spans):
    return ", ".join(f"{format_ts(a)}-{format_ts(b)} ({b - a:.0f} s)" for a, b in spans)


def fmt_db(db):
    if db is None:
        return "unreadable"
    if db == float("-inf") or db <= DIGITAL_DB:
        return "digital silence"
    return f"peak {db:.1f} dB"


# ── the whole pass ───────────────────────────────────────────────────────────

TRACKS = (("mic", "you", 2, "You", "MIC1"), ("usb", "caller", 3, "Caller", "USB"))


def run(name_dir, video, wavs, out=print, workdir=None):
    """Fill pass over one recording. Returns a summary dict, or None with no card take.

    wavs: {'you': path, 'caller': path, 'markers': path or None}.
    """
    take = card.find_card_take(name_dir)
    if take is None:
        return None
    own_tmp = workdir is None
    workdir = Path(workdir or tempfile.mkdtemp(prefix="cardfill_"))
    try:
        return _run(Path(name_dir), video, wavs, take, workdir, out)
    finally:
        if own_tmp:
            shutil.rmtree(workdir, ignore_errors=True)


def _run(name_dir, video, wavs, take, workdir, out):
    duration = wav_duration(wavs["you"])
    summary = {"tracks": {}, "markers": "OBS", "cues": [], "filled_caller": [],
               "health": [], "header": []}
    if take.unknown:
        out(f"card: ignoring {len(take.unknown)} unknown file(s) in card/: "
            + ", ".join(p.name for p in take.unknown))

    def role_path(role):
        paths = take.roles.get(role, [])
        if not paths:
            return None
        try:
            return card.join_parts(paths, workdir / f"{role}.joined.wav")
        except RuntimeError as e:
            out(f"card: could not join the {role} parts: {e}")
            return None

    def role_inputs(role):
        return card.input_args(take.roles.get(role, []), workdir / f"{role}.concat.txt")

    levels = {k: peak_db(wavs[k]) for k in ("you", "caller", "markers") if wavs.get(k)}
    mk_dead = wavs.get("markers") is None or levels.get("markers") is None or \
        levels["markers"] <= DIGITAL_DB

    result = align.align_take(
        obs_markers=None if mk_dead else wavs["markers"],
        card_pads=role_path("pads"),
        speech_pairs=[(wavs["you"], role_path("mic"), "Track 2 / MIC1"),
                      (wavs["caller"], role_path("usb"), "Track 3 / USB")],
        card_start=card.take_start(take),
        video_start=card.obs_start(video) if video else None)
    align.write_align(name_dir, result or {"offset": None, "method": None,
                                           "confidence": 0.0, "usable": False})
    summary["align"] = result
    out("card: " + align.describe(result))
    usable = bool(result and result.get("usable"))
    offset = float(result["offset"]) if usable else 0.0

    for role, key, track, label, cname in TRACKS:
        wav = wavs[key]
        digital = silences(wav, DIGITAL_DB, DIGITAL_MIN, duration)
        quiet = silences(wav, SILENCE_DB, DROPOUT_MIN, duration)
        cands = dropout_candidates(digital, quiet)
        info = {"level": levels.get(key), "dropouts": cands, "filled": [], "uncovered": cands}
        if cands and usable and take.roles.get(role):
            aligned = workdir / f"{role}.aligned.wav"
            try:
                render_aligned(role_inputs(role), offset, duration, aligned)
                card_quiet = silences(aligned, SILENCE_DB, CARD_QUIET_MIN, duration)
                filled, uncovered = split_by_card(cands, card_quiet)
                if filled:
                    splice(wav, aligned, filled)
                info.update(filled=filled, uncovered=uncovered)
            except (RuntimeError, ValueError, OSError, wave.Error) as e:
                out(f"card: could not fill Track {track} from {cname}: {e}")
        summary["tracks"][key] = info
        if key == "caller":
            summary["filled_caller"] = info["filled"]

        line = f"Track {track} ({label}): OBS {fmt_db(info['level'])}"
        quiet_both = len(info["uncovered"]) if usable else 0
        if info["filled"]:
            line += f", dropout filled from card {cname} at {fmt_spans(info['filled'])}"
        elif cands and not usable:
            line += f", silent at {fmt_spans(cands)}; card not lined up, not filled"
        else:
            line += ", no dropouts"
        if quiet_both:
            line += f" ({quiet_both} silent span(s) are silent on the card too: real quiet)"
        summary["health"].append(line)
        src = "OBS"
        if info["filled"]:
            src = f"OBS, card {cname} at {fmt_spans(info['filled'])}"
        summary["header"].append(f"# audio {label}: {src}")

    mk_line = f"Track 4 (markers): OBS {fmt_db(levels.get('markers'))}"
    if mk_dead and usable and take.roles.get("pads") and wavs.get("markers"):
        try:
            render_aligned(role_inputs("pads"), offset, duration, wavs["markers"],
                           loudnorm=False)
            summary["markers"] = "card SOUND_PAD"
            mk_line += "; presses read from the card pad file"
        except RuntimeError as e:
            out(f"card: could not rebuild the marker track from SOUND_PAD: {e}")
    if usable:
        summary["cues"] = cue_marks(take, offset, duration)
        if summary["cues"]:
            mk_line += f"; {len(summary['cues'])} PLAY mark(s) on the card"
    summary["health"].append(mk_line)
    marks = summary["markers"]
    if summary["cues"]:
        marks += f" + {len(summary['cues'])} card PLAY mark(s)"
    summary["header"].append(f"# markers: {marks}")
    summary["header"].append(f"# card take {take.take}: " + align.describe(result))
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fill OBS dropouts from the P4next card take.")
    ap.add_argument("name_dir")
    ap.add_argument("--video", default=None)
    ap.add_argument("--you", required=True)
    ap.add_argument("--caller", required=True)
    ap.add_argument("--markers", default=None)
    ap.add_argument("--report", default=None, help="write the health lines here")
    ap.add_argument("--header", default=None, help="write the transcript header lines here")
    ap.add_argument("--cues-out", default=None, help="write PLAY marks as 't,t,...' here")
    ap.add_argument("--filled-caller-out", default=None,
                    help="write the Track 3 spans filled from the card as 'a:b,...' here")
    args = ap.parse_args(argv)

    markers = args.markers if args.markers and Path(args.markers).exists() else None
    summary = run(args.name_dir, args.video,
                  {"you": args.you, "caller": args.caller, "markers": markers})
    if summary is None:
        return 0
    for line in summary["health"]:
        print(f"health: {line}")
    writes = [
        (args.report, "".join(f"{l}\n" for l in summary["health"])),
        (args.header, "".join(f"{l}\n" for l in summary["header"])),
        (args.cues_out, ",".join(f"{t:.3f}" for t in summary["cues"])),
        (args.filled_caller_out, ",".join(f"{a:.3f}:{b:.3f}" for a, b in summary["filled_caller"])),
    ]
    for path, text in writes:
        if path:
            try:
                Path(path).write_text(text, encoding="utf-8")
            except OSError as e:
                print(f"card: could not write {path}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
