"""
markers — fold Track-3 spoken markers into the transcript, inline.

The rig records a third audio track (0:a:2) fed only by pre-canned marker
phrases ("mark flag", "mark action", ...) triggered from the Stream Deck, routed
so the far side never hears them. Whisper transcribes that track like any other;
each recognized phrase becomes an inline marker line at its own timestamp.

No sidecar and no anchor: Track 3 shares the recording clock with the voice
tracks, so a phrase at 14:32 on Track 3 lines up with 14:32 in the transcript
for free.

Usage:
    python3 -m casting_call.markers <main_transcript.txt> <track3_transcript.txt>
"""

from __future__ import annotations

import sys
from pathlib import Path

from casting_call.transcript import parse_transcript, render_transcript

# Marker keywords, matched anywhere in the whisper output.
MARKER_TYPES = ["flag", "important", "action", "question", "quote"]

# Label for embedded marker lines. Deliberately not "MARK" — that collides with a
# real person named Mark once Caller lines get relabeled with names.
MARK_LABEL = "MARKER"


def match_marker(text):
    """Return the marker type found in the line, or None.

    Fuzzy on purpose: whisper may render "mark action item." or "Flag." so we
    look for the keyword anywhere in the (lowercased) line.
    """
    t = text.lower()
    for mtype in MARKER_TYPES:
        if mtype in t:
            return mtype
    return None


def markers_from_entries(track3_entries):
    """From parsed Track-3 entries, return [{'t','type','text'}] for recognized
    phrases. `text` is the marker type, e.g. 'flag'."""
    out = []
    for e in track3_entries:
        mtype = match_marker(e["text"])
        if mtype:
            out.append({"t": e["t"], "type": mtype, "text": mtype})
    return out


def embed_markers(transcript_entries, markers):
    """Return transcript_entries with marker lines merged in by timestamp.

    Each marker becomes an entry labeled MARK so it renders as a normal
    '[h:mm:ss] [MARKER] flag' line and reparses cleanly. Ties sort markers just
    after a same-second line so a flag lands on the moment it was pressed for.
    """
    combined = [dict(e, _m=0) for e in transcript_entries]
    combined += [{"t": m["t"], "label": MARK_LABEL, "text": m["text"], "_m": 1} for m in markers]
    combined.sort(key=lambda e: (e["t"], e["_m"]))
    return [{k: v for k, v in e.items() if k != "_m"} for e in combined]


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print("usage: python3 -m casting_call.markers <main_transcript> <track3_transcript>",
              file=sys.stderr)
        return 2
    main_path, track3_path = Path(argv[0]), Path(argv[1])

    track3 = parse_transcript(track3_path.read_text(encoding="utf-8"))
    markers = markers_from_entries(track3)
    if not markers:
        print("no markers recognized on Track 3")
        return 0

    entries = parse_transcript(main_path.read_text(encoding="utf-8"))
    merged = embed_markers(entries, markers)
    main_path.write_text(render_transcript(merged), encoding="utf-8")

    counts = {}
    for m in markers:
        counts[m["type"]] = counts.get(m["type"], 0) + 1
    summary = ", ".join(f"{n} {t}" for t, n in counts.items())
    print(f"embedded {len(markers)} marker(s): {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
