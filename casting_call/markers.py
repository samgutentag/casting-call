"""
markers — fold Track-3 spoken markers into the transcript, inline.

The rig records a third audio track (0:a:2) fed only by pre-canned marker
phrases ("mark topic", "mark action for me", ...) triggered from the Stream Deck,
routed so the far side never hears them. Whisper transcribes that track like any
other; each recognized phrase becomes an inline marker line at its own timestamp.

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

# Marker rules, in priority order: the first type whose keyword appears anywhere
# in the whisper output wins. Order is load-bearing, not cosmetic. The two action
# buttons speak "mark action for me" / "mark action for them", and both lines
# contain "action", so the owner keywords must be consulted before the bare
# "action" fallback or every press collapses into one bucket.
#
# "action" itself has no Stream Deck key. It exists only to catch a press where
# whisper clipped the unstressed trailing word ("...for me" -> "..."), so a
# degraded press surfaces as owner-unknown instead of being misfiled onto one
# side of the call.
MARKER_RULES = [
    ("action-them", "for them"),
    ("action-me", "for me"),
    ("topic", "topic"),
    ("important", "important"),
    ("question", "question"),
    ("quote", "quote"),
    ("video", "video"),
    ("action", "action"),
]

# Canonical type order, derived so it can never drift from the rules above.
# digest.py groups by this.
MARKER_TYPES = [mtype for mtype, _ in MARKER_RULES]

# Label for embedded marker lines. Deliberately not "MARK" — that collides with a
# real person named Mark once Caller lines get relabeled with names.
MARK_LABEL = "MARKER"


def match_marker(text):
    """Return the marker type found in the line, or None.

    Fuzzy on purpose: whisper may render "mark action item." or "Topic." so we
    look for the keyword anywhere in the (lowercased) line, taking the first
    rule that hits — see MARKER_RULES for why the order matters.
    """
    t = text.lower()
    for mtype, keyword in MARKER_RULES:
        if keyword in t:
            return mtype
    return None


def markers_from_entries(track3_entries):
    """From parsed Track-3 entries, return [{'t','type','text'}] for recognized
    phrases. `text` is the marker type, e.g. 'topic'."""
    out = []
    for e in track3_entries:
        mtype = match_marker(e["text"])
        if mtype:
            out.append({"t": e["t"], "type": mtype, "text": mtype})
    return out


def embed_markers(transcript_entries, markers):
    """Return transcript_entries with marker lines merged in by timestamp.

    Each marker becomes an entry labeled MARK so it renders as a normal
    '[h:mm:ss] [MARKER] topic' line and reparses cleanly. Ties sort markers just
    after a same-second line so a press lands on the moment it was pressed for.
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
