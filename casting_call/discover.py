"""
discover — resolve a path into the call transcripts under it.

A day directory holds more `.txt` files than it holds calls. `parts/` carries the
per-track reference transcripts (`you.txt`, `caller.txt`, `markers.txt`), which
are three garbage "calls" per recording if you glob naively. The same call can
also sit in two places at once, because the layout changed: the current pipeline
writes `<day>/<name>/<name>.txt`, the old one wrote `<day>/transcripts/<name>.txt`,
and `audio_only/` holds copies of some of those.

On top of that a call can have variants: `<name>.txt`, `<name>-clean.txt`,
`<name>-stitched.txt`. Those are the same conversation processed differently, so
only one should become a report.

Usage:
    from casting_call.discover import find_transcripts, group_by_day
"""

from __future__ import annotations

from pathlib import Path

# Directories that never hold a call transcript.
SKIP_DIRS = {"parts", "frames", "review"}

# Suffixes that mark a derived or partial file rather than a call.
SKIP_SUFFIXES = ("-left", "-right")

# Variant ranking for one call: the most-processed form wins. A stitched
# transcript exists because the audio failed, so it beats the raw one too.
VARIANTS = ["-clean", "-stitched", ""]

# Which parent directory wins when the same call sits in two layouts. The
# per-recording folder is the current one; earlier names trail it.
DIR_RANK = ["transcripts", "audio_only"]


def _is_candidate(p):
    if p.suffix != ".txt":
        return False
    if any(part in SKIP_DIRS for part in p.parts):
        return False
    return not p.stem.endswith(SKIP_SUFFIXES)


def _call_key(p):
    """The call a file belongs to: its stem minus any variant suffix."""
    stem = p.stem
    for v in VARIANTS:
        if v and stem.endswith(v):
            return stem[: -len(v)]
    return stem


def _rank(p):
    """Sort key for competing files for one call. Lower is better."""
    stem = p.stem
    variant = next((n for n, v in enumerate(VARIANTS) if v and stem.endswith(v)),
                   len(VARIANTS) - 1)
    parent = p.parent.name
    # A per-recording folder is named after the call itself, so anything not in
    # DIR_RANK is the current layout and sorts ahead of the legacy directories.
    directory = DIR_RANK.index(parent) + 1 if parent in DIR_RANK else 0
    return (variant, directory, str(p))


def find_transcripts(target):
    """Every call transcript under `target`, one file per call, sorted by path.

    `target` may be a single `.txt` (returned as-is), a day directory, or a
    parent of day directories.
    """
    target = Path(target)
    if target.is_file():
        return [target]

    by_call = {}
    for p in sorted(target.rglob("*.txt")):
        if not _is_candidate(p):
            continue
        key = (p.parent.parent if p.parent.name in DIR_RANK else p.parent.parent,
               _call_key(p))
        best = by_call.get(key)
        if best is None or _rank(p) < _rank(best):
            by_call[key] = p
    return sorted(by_call.values())


def group_by_day(paths, target):
    """Group transcripts into (day_name, [paths]) pairs, in day order.

    The day is the directory directly under `target`, or `target` itself when it
    is already a single day's directory.
    """
    target = Path(target)
    rels = []
    for p in paths:
        try:
            rels.append((p, p.relative_to(target)))
        except ValueError:
            rels.append((p, Path(p.name)))

    # Decide once for the whole target rather than per path. Both layouts put a
    # transcript two levels under its day (<day>/<name>/<name>.txt and the older
    # <day>/transcripts/<name>.txt), so anything three deep means `target` is a
    # parent of day directories, and anything shallower means it IS one.
    deepest = max((len(r.parts) for _, r in rels), default=0)
    parent_of_days = deepest >= 3

    days = {}
    for p, rel in rels:
        day = rel.parts[0] if parent_of_days else target.name
        days.setdefault(day, []).append(p)
    return [(d, days[d]) for d in sorted(days)]
