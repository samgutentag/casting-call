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


def merge_rollups(digests):
    """One roll-up across several calls, each item tagged with its call.

    Order follows the calls as given, then the order within each call, so the
    merged list reads chronologically for a day processed in time order.
    """
    out = {b: [] for b in BUCKETS}
    for d in digests:
        for b in BUCKETS:
            for item in d.get("rollup", {}).get(b, []):
                out[b].append(dict(item, call=d.get("name")))
    return out


def _label(day):
    """'26-08-25' -> 'August 25, 2026'. Anything else passes through."""
    try:
        parsed = _dt.datetime.strptime(day, "%y-%m-%d").date()
    except (ValueError, TypeError):
        return day
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"


def day_summary(day, digests):
    """Everything a day page needs, plus what the index needs to link to it."""
    merged = merge_rollups(digests)
    return {
        "day": day,
        "label": _label(day),
        "calls": len(digests),
        "markers": sum(d.get("total", 0) for d in digests),
        "asides": sum(d.get("aside_total", 0) for d in digests),
        "rollup": merged,
        "counts": {b: len(merged[b]) for b in BUCKETS},
        "calls_list": digests,
    }
