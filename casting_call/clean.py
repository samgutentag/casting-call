"""
clean — strip whisper hallucinations from a woven transcript.

Removes lines that fall inside a silent span for their channel (whisper invents
speech on dead audio) plus a curated set of known junk phrases. Rewrites the
transcript in place.

Usage:
    python3 -m casting_call.clean transcript.txt \
        --silent-you "start:end,start:end" \
        --silent-caller "872.8:2085.2"

Spans are seconds, comma-separated, each as start:end. Either may be omitted.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from casting_call.transcript import (
    parse_transcript, render_transcript, strip_silent_lines, strip_junk_lines,
)


def parse_spans(s):
    spans = []
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        start, end = part.split(":")
        spans.append((float(start), float(end)))
    return spans


def main(argv=None):
    ap = argparse.ArgumentParser(description="Strip whisper hallucinations from a transcript.")
    ap.add_argument("transcript")
    ap.add_argument("--you-label", default="You")
    ap.add_argument("--caller-label", default="Caller")
    ap.add_argument("--silent-you", default="", help="silent spans on your track, 'start:end,...'")
    ap.add_argument("--silent-caller", default="", help="silent spans on the far-side track")
    args = ap.parse_args(argv)

    path = Path(args.transcript)
    entries = parse_transcript(path.read_text(encoding="utf-8"))
    before = len(entries)

    spans_by_label = {
        args.you_label: parse_spans(args.silent_you),
        args.caller_label: parse_spans(args.silent_caller),
    }
    entries = strip_silent_lines(entries, spans_by_label)
    entries = strip_junk_lines(entries)

    path.write_text(render_transcript(entries), encoding="utf-8")
    removed = before - len(entries)
    print(f"cleaned {path.name}: dropped {removed} line(s) ({len(entries)} kept)")


if __name__ == "__main__":
    main()
