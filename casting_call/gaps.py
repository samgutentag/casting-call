"""
gaps — find stretches where one channel produced no transcript at all.

Silence detection alone misses a far-side dropout when the dead track still
carries sound. On 2026-10-20 Corey had music playing; his capture stopped
delivering speech at 8:18, but the track sat around -30 dB for the rest of the
call, so no silent span ever crossed the caption-backup threshold and 27
minutes of his side were lost. A channel that stops producing lines is the
symptom that survives background noise, so ripa checks both and hands the
union to the caption backup.

A gap that is real quiet (Sam presenting for two minutes) costs one OCR pass;
backup.py reads the captions, sees no one else talking, and splices nothing.

Usage:
    python3 -m casting_call.gaps <transcript.txt> --label Caller \\
        --min 120 --duration 2139 [--also "312.5:871.0,1500:1620"]
Prints the merged windows as "start:end,..." (empty if none).
"""

from __future__ import annotations

import sys
from pathlib import Path

from .backup import parse_windows
from .transcript import parse_transcript

LINE_SECONDS = 10   # a whisper line covers about this long after its timestamp


def transcript_gaps(entries, label, min_gap, duration):
    """Spans of at least min_gap seconds with no line from label, as (start, end)."""
    times = sorted(e['t'] for e in entries if e['label'] == label)
    gaps, covered = [], 0.0
    for t in times + [duration]:
        if t - covered >= min_gap:
            gaps.append((covered, float(t)))
        covered = max(covered, t + LINE_SECONDS)
    return gaps


def merge_windows(*groups):
    """Union of (start, end) spans, overlapping or touching ones joined."""
    spans = sorted(s for g in groups for s in g)
    out = []
    for a, b in spans:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def format_windows(spans):
    return ','.join(f'{a:.3f}:{b:.3f}' for a, b in spans)


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description='Windows where a channel has no transcript.')
    ap.add_argument('transcript')
    ap.add_argument('--label', default='Caller')
    ap.add_argument('--min', type=float, default=120.0)
    ap.add_argument('--duration', type=float, required=True)
    ap.add_argument('--also', default='', help='spans to union in, "start:end,..."')
    args = ap.parse_args(argv)

    entries = parse_transcript(Path(args.transcript).read_text())
    gaps = transcript_gaps(entries, args.label, args.min, args.duration)
    print(format_windows(merge_windows(gaps, parse_windows(args.also))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
