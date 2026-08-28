"""
rollupday — aggregate per-call digests into a day, and days into an index.

The per-call page answers "what happened on this call". Running a whole
directory raises two more questions that no single call can answer: what do I
owe across the day, and which day was that thing in. This module builds the
data behind those two pages. It does no language work and no rendering.

Usage:
    from casting_call.rollupday import day_summary, merge_rollups
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

BUCKETS = ("yours", "theirs", "questions", "unowned")


def report_name(transcript_path):
    """The per-call report filename for a transcript, as a bare name."""
    return Path(transcript_path).stem + "-call.html"


# Which bucket a type belongs in. Used when a remap moves an item between them.
BUCKET_FOR = {"action-me": "yours", "action-them": "theirs",
              "question": "questions", "action": "unowned"}


def merge_rollups(digests, remap=None):
    """One roll-up across several calls, each item tagged with its call.

    Order follows the calls as given, then the order within each call, so the
    merged list reads chronologically for a day processed in time order.

    `remap` re-types items before bucketing, for calls recorded before the marker
    buttons were split by owner. Their single generic Action key meant Sam's own
    to-do, so `{"action": "action-me"}` moves those out of "unowned" and into
    "yours". Without it a day page reads "Nothing landed on you" while the items
    sit in a bucket no day template prints, which is the one thing these pages
    must never do.
    """
    remap = remap or {}
    out = {b: [] for b in BUCKETS}
    for d in digests:
        for b in BUCKETS:
            for item in d.get("rollup", {}).get(b, []):
                new_type = remap.get(item.get("type"), item.get("type"))
                bucket = BUCKET_FOR.get(new_type, b)
                out[bucket].append(dict(item, type=new_type, call=d.get("name")))
    return out


def _label(day):
    """'26-08-25' -> 'August 25, 2026'. Anything else passes through."""
    try:
        parsed = _dt.datetime.strptime(day, "%y-%m-%d").date()
    except (ValueError, TypeError):
        return day
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"


def day_summary(day, digests, remap=None):
    """Everything a day page needs, plus what the index needs to link to it.

    `remap` is applied to the merged roll-up AND to each call's own roll-up in
    `calls_list`, because the day page's per-call tallies are read from those and
    have to agree with the roll-up printed above them.
    """
    merged = merge_rollups(digests, remap)
    calls_list = digests
    if remap:
        calls_list = [dict(d, rollup=merge_rollups([d], remap)) for d in digests]
    return {
        "day": day,
        "label": _label(day),
        "calls": len(digests),
        "markers": sum(d.get("total", 0) for d in digests),
        "asides": sum(d.get("aside_total", 0) for d in digests),
        "rollup": merged,
        "counts": {b: len(merged[b]) for b in BUCKETS},
        "calls_list": calls_list,
    }
