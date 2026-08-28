"""
asides — the phrases Sam speaks on his own track to flag something live.

The Stream Deck covers the deliberate marker channel (see `markers`), but Sam
also flags things out loud, usually while muted: "action item, send the SOW",
"notes notes notes, the nav labels are stale". Those land on Track 1 like any
other speech, so they arrive in the merged transcript as ordinary `[You]` lines.

This module finds them deterministically. It used to live as a phrase list in
the process-transcript skill's prose, which meant the model re-interpreted it on
every run and a borderline line could be flagged one day and missed the next.

What it deliberately does NOT do: infer. A bare "I'll send that over" is a
commitment, not a flag, and Sam says those constantly in a normal sync. Catching
them here would flood the authoritative channel with the same items the model's
inferred pass already finds. Unflagged commitments stay the model's job.

Usage:
    from casting_call.asides import asides_from_entries
"""

from __future__ import annotations

# Phrase rules, checked in order, first hit wins. Longest/most specific first
# where two could both match the same line.
ASIDE_RULES = [
    ("note", "notes notes notes"),
    ("note", "note to self"),
    ("note", "make a note"),
    ("note", "i'll note that"),
    ("note", "ill note that"),
    ("note", "taking a note of"),
    ("note", "i'm noting this"),
    ("note", "im noting this"),
    ("note", "note that"),
    ("action", "action item"),
    ("action", "this is for me to do"),
]

# The transcript label for Sam's own mic. Asides are only ever read off this
# channel: the far side saying "action item" is their meeting, not his flag.
SELF_LABEL = "You"


def match_aside(text):
    """Return ('note' | 'action') for a deliberately spoken flag, else None.

    Matched anywhere in the line, because Sam says these mid-sentence. Returns
    the type only; use `matched_phrase` when the trigger itself is needed.
    """
    hit = matched_phrase(text)
    return hit[0] if hit else None


def matched_phrase(text):
    """Return (type, phrase) for the first rule that hits, else None."""
    t = text.lower()
    for atype, phrase in ASIDE_RULES:
        if phrase in t:
            return (atype, phrase)
    return None


def asides_from_entries(entries, self_label=SELF_LABEL):
    """From parsed transcript entries, return the flagged `[You]` lines.

    Each result is {'t', 'type', 'text', 'phrase'}: the timestamp of the line it
    came from (exact, since it is one specific line), the flag type, the full
    line, and the trigger phrase that matched. Trimming the trigger down to the
    substance ("the nav labels are stale") is language work and stays with the
    model, so the whole line is handed over intact.
    """
    out = []
    for e in entries:
        if e["label"] != self_label:
            continue
        hit = matched_phrase(e["text"])
        if hit:
            atype, phrase = hit
            out.append({"t": e["t"], "type": atype, "text": e["text"], "phrase": phrase})
    return out
