"""
align — line a P4next card take up with the OBS video.

One number comes out: the offset in seconds such that

    card time = video time + offset

plus the method that found it and a confidence from 0 to 1, written to
<name>/align.json. Three methods, best first:

    markers  pad A bursts on the card's SOUND_PAD file against OBS Track 4.
             Both come from the same press through the same box, so every burst
             matched to the millisecond on the 2026-11-14 test call (1.137 s).
    speech   cross-correlate speech envelopes: OBS Track 2 against MIC1, then
             Track 3 against USB, over the first 10 minutes. A coarse pass at
             50 Hz finds the lag, a 1 kHz pass refines it.
    clock    card bext start against the time in the .mkv file name. The
             P4next clock is set by hand (23 s off on the test call), so this is
             approximate and always lands under the confidence floor.

Below CONFIDENCE_FLOOR the result is still written and reported, but `usable`
is false and nothing is spliced from the card.

Pure standard library: the envelope cross-correlation uses a small radix-2 FFT.

Usage:
    python3 -m casting_call.align offset <name_dir>   # prints the offset if usable
"""

from __future__ import annotations

import argparse
import array
import cmath
import json
import math
import subprocess
import sys
from pathlib import Path

from casting_call.marker_track import MIN_PRESS, detect_bursts

CONFIDENCE_FLOOR = 0.5
CLOCK_CONFIDENCE = 0.3

# Two onsets from the same press agree to well under this.
MATCH_TOL = 0.02
# Card and OBS are started by hand within a minute or so of each other; this
# only rules out nonsense pairings on a long call.
MAX_OFFSET = 1800.0

SPEECH_WINDOW = 600.0
COARSE_HZ = 50
FINE_HZ = 1000
FINE_SPAN = 60.0      # seconds of the busiest stretch used for the 1 kHz pass
FINE_SEARCH = 0.03    # +/- seconds around the coarse lag (one coarse step is 0.02)


# ── markers ──────────────────────────────────────────────────────────────────

def onsets(bursts, min_press=MIN_PRESS):
    return [s for s, e in bursts if e - s >= min_press]


def offset_from_onsets(obs, card, tol=MATCH_TOL, max_offset=MAX_OFFSET):
    """(offset, matched, confidence) from two onset lists, or None.

    Tries every pairing as a candidate offset, keeps the one that lines up the
    most onsets, and refines it to the median difference of the matched pairs.
    Confidence is matched / the shorter list, so stray bursts on one side do not
    sink a clean match but a lone coincidence among many presses does not pass.
    """
    if not obs or not card:
        return None
    best = None
    for a in obs:
        for b in card:
            d = b - a
            if abs(d) > max_offset:
                continue
            pairs = []
            for x in obs:
                near = min(card, key=lambda y: abs(y - (x + d)))
                if abs(near - (x + d)) <= tol:
                    pairs.append(near - x)
            if best is None or len(pairs) > len(best[1]) or \
                    (len(pairs) == len(best[1]) and abs(d) < abs(best[0])):
                best = (d, pairs)
    if best is None or not best[1]:
        return None
    diffs = sorted(best[1])
    mid = len(diffs) // 2
    offset = diffs[mid] if len(diffs) % 2 else (diffs[mid - 1] + diffs[mid]) / 2
    matched = len(diffs)
    return offset, matched, matched / min(len(obs), len(card))


def align_markers(obs_marker_audio, card_pads_audio, detect=detect_bursts):
    """Markers method result dict, or None when either side has no presses."""
    obs = onsets(detect(obs_marker_audio))
    card = onsets(detect(card_pads_audio))
    got = offset_from_onsets(obs, card)
    if got is None:
        return None
    offset, matched, conf = got
    return {"offset": round(offset, 4), "method": "markers", "confidence": round(conf, 3),
            "approximate": False,
            "detail": f"{matched} of {len(obs)} OBS / {len(card)} card pad presses matched"}


# ── speech ───────────────────────────────────────────────────────────────────

def _fft(a, invert=False):
    """In-place iterative radix-2 FFT over a list of complex. len(a) is a power of 2."""
    n = len(a)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    length = 2
    while length <= n:
        ang = (2 if invert else -2) * math.pi / length
        wl = cmath.exp(1j * ang)
        half = length // 2
        tw = [1 + 0j] * half
        for k in range(1, half):
            tw[k] = tw[k - 1] * wl
        for start in range(0, n, length):
            for k in range(half):
                u = a[start + k]
                v = a[start + k + half] * tw[k]
                a[start + k] = u + v
                a[start + k + half] = u - v
        length <<= 1
    if invert:
        for i in range(n):
            a[i] /= n
    return a


def xcorr_lag(x, y):
    """Lag L (in samples) maximizing sum x[t] * y[t + L], plus the peak's Pearson r.

    Both inputs are mean-removed first. Positive L means y runs later than x.
    """
    if not x or not y:
        return 0, 0.0
    mx, my = sum(x) / len(x), sum(y) / len(y)
    xs = [v - mx for v in x]
    ys = [v - my for v in y]
    n = 1
    while n < len(xs) + len(ys):
        n <<= 1
    fx = _fft([complex(v) for v in xs] + [0j] * (n - len(xs)))
    fy = _fft([complex(v) for v in ys] + [0j] * (n - len(ys)))
    corr = _fft([b * a.conjugate() for a, b in zip(fx, fy)], invert=True)
    best_i = max(range(n), key=lambda i: corr[i].real)
    lag = best_i if best_i < n // 2 else best_i - n
    return lag, pearson_at(xs, ys, lag)


def pearson_at(x, y, lag):
    """Pearson r of x[t] against y[t + lag] over their overlap."""
    lo = max(0, -lag)
    hi = min(len(x), len(y) - lag)
    if hi - lo < 2:
        return 0.0
    xa = x[lo:hi]
    ya = y[lo + lag:hi + lag]
    mx, my = sum(xa) / len(xa), sum(ya) / len(ya)
    sxy = sum((a - mx) * (b - my) for a, b in zip(xa, ya))
    sxx = sum((a - mx) ** 2 for a in xa)
    syy = sum((b - my) ** 2 for b in ya)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sxy / math.sqrt(sxx * syy)


def envelope(path, hz=FINE_HZ, seconds=SPEECH_WINDOW, decode_rate=8000):
    """Mean absolute level at `hz` points per second over the first `seconds`.

    [] when ffmpeg cannot read the file.
    """
    cmd = ["ffmpeg", "-v", "error", "-t", str(seconds), "-i", str(path),
           "-ac", "1", "-ar", str(decode_rate), "-f", "s16le", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True)
    except (FileNotFoundError, OSError):
        return []
    if proc.returncode != 0:
        return []
    samples = array.array("h")
    raw = proc.stdout
    samples.frombytes(raw[: len(raw) - len(raw) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    step = max(1, decode_rate // hz)
    return [sum(map(abs, samples[i:i + step])) / step
            for i in range(0, len(samples) - step + 1, step)]


def decimate(env, factor):
    return [sum(env[i:i + factor]) / factor for i in range(0, len(env) - factor + 1, factor)]


def offset_from_envelopes(obs_env, card_env, hz=FINE_HZ, coarse_hz=COARSE_HZ):
    """(offset seconds, r) from two envelopes at `hz`, or None if either is flat."""
    if not obs_env or not card_env or max(obs_env) <= 0 or max(card_env) <= 0:
        return None
    factor = max(1, hz // coarse_hz)
    lag, r = xcorr_lag(decimate(obs_env, factor), decimate(card_env, factor))
    coarse = lag * factor
    # Refine on the busiest FINE_SPAN of the OBS side, at full rate.
    span = int(FINE_SPAN * hz)
    if len(obs_env) > span:
        win = [sum(obs_env[i:i + hz]) for i in range(0, len(obs_env) - hz, hz)]
        start = max(range(len(win)), key=lambda i: win[i]) * hz
        start = max(0, min(start - span // 2, len(obs_env) - span))
    else:
        start, span = 0, len(obs_env)
    seg = obs_env[start:start + span]
    search = int(FINE_SEARCH * hz)
    best_lag, best_r = coarse, None
    for lg in range(coarse - search, coarse + search + 1):
        rr = pearson_at(seg, card_env, start + lg)
        if best_r is None or rr > best_r:
            best_lag, best_r = lg, rr
    if best_r is not None:
        r = max(r, best_r)
    return best_lag / hz, r


def align_speech(pairs, env=envelope):
    """Speech method over [(obs path, card path, label)], first pair that clears the floor."""
    tried = None
    for obs_path, card_path, label in pairs:
        if not obs_path or not card_path:
            continue
        got = offset_from_envelopes(env(obs_path), env(card_path))
        if got is None:
            continue
        offset, r = got
        res = {"offset": round(offset, 4), "method": "speech",
               "confidence": round(max(0.0, r), 3), "approximate": False,
               "detail": f"{label} envelopes, r={r:.2f}"}
        if r >= CONFIDENCE_FLOOR:
            return res
        tried = tried or res
    return tried


# ── clock ────────────────────────────────────────────────────────────────────

def align_clock(card_start, video_start):
    """Clock method: the card started (video_start - card_start) seconds earlier."""
    if card_start is None or video_start is None:
        return None
    offset = (video_start - card_start).total_seconds()
    return {"offset": round(offset, 3), "method": "clock", "confidence": CLOCK_CONFIDENCE,
            "approximate": True,
            "detail": f"card bext {card_start:%H:%M:%S} vs video name {video_start:%H:%M:%S}"}


# ── together ─────────────────────────────────────────────────────────────────

def choose(results):
    """First result at or above the floor, else the most confident one, else None."""
    results = [r for r in results if r]
    for r in results:
        if r["confidence"] >= CONFIDENCE_FLOOR:
            return dict(r, usable=True)
    if not results:
        return None
    best = max(results, key=lambda r: r["confidence"])
    return dict(best, usable=False)


def align_take(obs_markers=None, card_pads=None, speech_pairs=(), card_start=None,
               video_start=None, detect=detect_bursts, env=envelope):
    """Run the methods best first and stop at the first usable one."""
    tried = []
    if obs_markers and card_pads:
        tried.append(align_markers(obs_markers, card_pads, detect=detect))
        if tried[-1] and tried[-1]["confidence"] >= CONFIDENCE_FLOOR:
            return choose(tried)
    if speech_pairs:
        tried.append(align_speech(speech_pairs, env=env))
        if tried[-1] and tried[-1]["confidence"] >= CONFIDENCE_FLOOR:
            return choose(tried)
    tried.append(align_clock(card_start, video_start))
    return choose(tried)


def write_align(name_dir, result):
    path = Path(name_dir) / "align.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return path


def load_align(name_dir):
    """align.json as a dict, or None if absent or unreadable."""
    try:
        return json.loads((Path(name_dir) / "align.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def describe(result):
    if not result:
        return "card could not be lined up with the video"
    tag = "" if result.get("usable") else ", below the confidence floor, not used"
    approx = " (approximate)" if result.get("approximate") else ""
    return (f"card offset {result['offset']:+.3f} s by {result['method']}{approx}, "
            f"confidence {result['confidence']:.2f}{tag}: {result.get('detail', '')}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Card to video alignment.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("offset", help="print the usable offset from align.json, if any")
    o.add_argument("name_dir")
    args = ap.parse_args(argv)
    res = load_align(args.name_dir)
    if not res or not res.get("usable"):
        return 1
    print(f"{float(res['offset']):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
