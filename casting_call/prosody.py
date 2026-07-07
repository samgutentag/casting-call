"""Per-utterance prosody features from one speaker's audio channel.

The transcript tells us when a speaker talked; this module measures how.
For each utterance we extract pitch (median, range, terminal slope), energy,
and voiced fraction with Praat (via parselmouth), then z-score every feature
against that speaker's own baseline across the call. Within-speaker
normalization is the whole trick: +1.5 sigma of pitch range means "animated
for them," which is the only comparison that means anything.

Interpretation guide (all z-scores, all within-speaker):
  f0_med    higher = more excited/aroused delivery
  f0_range  wider = more animated, flatter = more guarded/bored
  f0_slope  terminal pitch direction. Rising on a short affirmation reads as
            enthusiasm/invitation; falling reads as closure (fine) or
            flatness (on long turns, judge with energy)
  energy    louder-for-them = engaged
  rate      words/sec when the segment has text

None of this is ground truth. It is a heatmap over the call that tells you
which moments to re-listen to.
"""
from dataclasses import dataclass, asdict
import math
import statistics

import parselmouth

PITCH_FLOOR = 75.0    # Hz — standard Praat range, fine for adult speech
PITCH_CEIL = 500.0
MIN_VOICED_FRAMES = 8  # below this, pitch stats are noise; features become None
TERMINAL_PORTION = 0.4  # fit the slope over the final 40% of voiced frames
BASELINE_MIN_DUR = 1.5  # baseline stats come from utterances at least this long;
                        # backchannels ("yeah") are tiny samples that skew the mean


@dataclass
class Utterance:
    t_start: float
    t_end: float
    label: str
    text: str


@dataclass
class ProsodyFeatures:
    f0_med: float | None = None      # Hz
    f0_range: float | None = None    # Hz, p90 - p10
    f0_slope: float | None = None    # Hz/sec over the terminal portion
    energy: float | None = None      # dB (Praat intensity mean)
    voiced_frac: float | None = None
    rate: float | None = None        # words / sec


def utterances_from_transcript(entries, label, max_gap=15.0, min_len=0.6):
    """Build utterance windows for one label from parsed transcript entries.

    Each entry's window runs from its timestamp to the next entry's timestamp
    (any speaker), capped at max_gap seconds — transcript lines only carry a
    start time, so the next line is our best end marker.
    """
    utts = []
    for i, entry in enumerate(entries):
        if entry['label'] != label:
            continue
        t0 = float(entry['t'])
        t1 = t0 + max_gap
        if i + 1 < len(entries):
            t1 = min(t1, float(entries[i + 1]['t']))
        if t1 - t0 >= min_len:
            utts.append(Utterance(t0, t1, entry['label'], entry['text']))
    return utts


def utterances_from_vad(wav_path, label='?', min_dur=0.8, max_dur=30.0,
                        silence_bridge=0.6):
    """Segment a channel by voiced-region detection (fallback when the
    transcript has no usable lines for this speaker)."""
    snd = parselmouth.Sound(str(wav_path))
    pitch = snd.to_pitch(pitch_floor=PITCH_FLOOR, pitch_ceiling=PITCH_CEIL)
    step = pitch.time_step
    voiced = [(pitch.get_time_from_frame_number(i + 1))
              for i in range(pitch.get_number_of_frames())
              if not math.isnan(pitch.get_value_in_frame(i + 1) or float('nan'))]
    utts = []
    start = prev = None
    for t in voiced:
        if start is None:
            start = prev = t
            continue
        if t - prev > silence_bridge or t - start > max_dur:
            if prev - start >= min_dur:
                utts.append(Utterance(start, prev + step, label, ''))
            start = t
        prev = t
    if start is not None and prev - start >= min_dur:
        utts.append(Utterance(start, prev + step, label, ''))
    return utts


def _extract(snd, utt):
    part = snd.extract_part(from_time=utt.t_start, to_time=utt.t_end,
                            preserve_times=False)
    feats = ProsodyFeatures()

    pitch = part.to_pitch(pitch_floor=PITCH_FLOOR, pitch_ceiling=PITCH_CEIL)
    pts = []
    for i in range(pitch.get_number_of_frames()):
        f0 = pitch.get_value_in_frame(i + 1)
        if f0 and not math.isnan(f0):
            pts.append((pitch.get_time_from_frame_number(i + 1), f0))
    total_frames = max(1, pitch.get_number_of_frames())
    feats.voiced_frac = len(pts) / total_frames

    if len(pts) >= MIN_VOICED_FRAMES:
        f0s = sorted(f0 for _, f0 in pts)
        feats.f0_med = statistics.median(f0s)
        lo = f0s[max(0, int(0.10 * len(f0s)) - 1)]
        hi = f0s[min(len(f0s) - 1, int(0.90 * len(f0s)))]
        feats.f0_range = hi - lo
        tail = pts[int(len(pts) * (1 - TERMINAL_PORTION)):]
        if len(tail) >= 3:
            feats.f0_slope = _linfit_slope(tail)

    # Energy over the voiced stretch only. The utterance window is bounded by
    # the NEXT transcript line, so it often trails off into silence; averaging
    # over that silence reads a normal goodbye as an energy collapse.
    intensity = part.to_intensity(minimum_pitch=PITCH_FLOOR)
    lo = pts[0][0] if pts else None
    hi = pts[-1][0] if pts else None
    vals = []
    for t in intensity.xs():
        if lo is not None and not (lo <= t <= hi):
            continue
        v = intensity.get_value(t)
        if v and not math.isnan(v):
            vals.append(v)
    if vals:
        feats.energy = sum(vals) / len(vals)

    dur = utt.t_end - utt.t_start
    words = len(utt.text.split())
    if words and dur > 0:
        feats.rate = words / dur
    return feats


def _linfit_slope(points):
    """Least-squares slope of (t, f0) points, Hz per second."""
    n = len(points)
    mt = sum(t for t, _ in points) / n
    mf = sum(f for _, f in points) / n
    denom = sum((t - mt) ** 2 for t, _ in points)
    if denom == 0:
        return 0.0
    return sum((t - mt) * (f - mf) for t, f in points) / denom


def analyze(wav_path, utterances):
    """Return [(Utterance, ProsodyFeatures, zscores_dict)] for one channel.

    z-scores are computed per feature against this utterance set (i.e. the
    speaker's own baseline for the call). Features that are None stay None.
    """
    snd = parselmouth.Sound(str(wav_path))
    feats = [_extract(snd, u) for u in utterances]

    # Baseline from substantive utterances only: short backchannels are tiny,
    # noisy samples that drag the mean around. They still get SCORED against
    # the baseline; they just don't define it. Fall back to everything when a
    # call is nothing but backchannel.
    substantive = [f for u, f in zip(utterances, feats)
                   if (u.t_end - u.t_start) >= BASELINE_MIN_DUR]
    keys = ['f0_med', 'f0_range', 'f0_slope', 'energy', 'rate']
    stats = {}
    for k in keys:
        vals = [getattr(f, k) for f in substantive if getattr(f, k) is not None]
        if len(vals) < 3:
            vals = [getattr(f, k) for f in feats if getattr(f, k) is not None]
        if len(vals) >= 3:
            mean = sum(vals) / len(vals)
            sd = statistics.pstdev(vals)
            stats[k] = (mean, sd if sd > 1e-9 else None)

    out = []
    for utt, f in zip(utterances, feats):
        z = {}
        for k in keys:
            v = getattr(f, k)
            if v is not None and k in stats and stats[k][1]:
                z[k] = (v - stats[k][0]) / stats[k][1]
        out.append((utt, f, z))
    return out


def format_annotation(z, slope_hz=None):
    """Compact affect tag like '{pitch +1.4σ rising · energy +0.9σ}'."""
    bits = []
    if 'f0_med' in z and abs(z['f0_med']) >= 0.5:
        bits.append(f"pitch {z['f0_med']:+.1f}σ")
    if slope_hz is not None and abs(slope_hz) >= 20:
        bits.append('rising' if slope_hz > 0 else 'falling')
    if 'f0_range' in z and abs(z['f0_range']) >= 0.75:
        bits.append(f"animation {z['f0_range']:+.1f}σ")
    if 'energy' in z and abs(z['energy']) >= 0.75:
        bits.append(f"energy {z['energy']:+.1f}σ")
    if 'rate' in z and abs(z['rate']) >= 1.0:
        bits.append(f"pace {z['rate']:+.1f}σ")
    return '{' + ' · '.join(bits) + '}' if bits else ''


def annotate_transcript(entries, analyzed):
    """Merge analyzed prosody back into transcript lines by start time."""
    by_t = {round(u.t_start): (f, z) for u, f, z in analyzed}
    lines = []
    for entry in entries:
        h, rem = divmod(entry['t'], 3600)
        m, s = divmod(rem, 60)
        stamp = f"[{int(h)}:{int(m):02d}:{int(s):02d}]"
        line = f"{stamp} [{entry['label']}] {entry['text']}"
        hit = by_t.get(round(entry['t']))
        if hit:
            f, z = hit
            tag = format_annotation(z, f.f0_slope)
            if tag:
                line += f"  {tag}"
        lines.append(line)
    return '\n'.join(lines)
