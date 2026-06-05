import re

LINE_RE = re.compile(r'^\[(\d+):(\d+):(\d+)\] \[([^\]]+)\] (.*)$')


def parse_transcript(text):
    """Parse '[h:mm:ss] [Label] text' lines into dicts with seconds 't'."""
    entries = []
    for line in text.splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        h, mn, sec = int(m[1]), int(m[2]), int(m[3])
        entries.append({'t': h * 3600 + mn * 60 + sec, 'label': m[4], 'text': m[5]})
    return entries


def _name_at(spans, t):
    for span in spans:
        if span.start <= t < span.end:
            return span.name  # None for unresolved/gap
    return None


def relabel_transcript(entries, spans, lag_seconds, self_name, caller_label='Caller'):
    """Relabel only caller_label lines using the timeline at (t - lag).

    Self-name spans are bleed -> stay caller_label. Unresolved/gap/off-timeline
    -> stay caller_label. Other labels (e.g. You) are untouched. Each result
    carries 'original_label' (pre-relabel) for coverage.
    """
    out = []
    for entry in entries:
        result = dict(entry)
        result['original_label'] = entry['label']
        if entry['label'] == caller_label:
            who = _name_at(spans, entry['t'] - lag_seconds)
            if who and who != self_name:
                result['label'] = who
        out.append(result)
    return out


def distinct_named_speakers(spans, self_name):
    """Sorted distinct named speakers in the timeline, excluding self.

    Unresolved/gap spans have name=None and are excluded, so OCR noise (which
    resolves to None, never a roster name) cannot fake an extra speaker.
    """
    return sorted({s.name for s in spans if s.name and s.name != self_name})


def collapse_caller(entries, name, caller_label='Caller'):
    """Relabel EVERY caller_label line to `name`. Used on a 2-person call where
    the one other speaker is the only possible Caller, so timing is irrelevant.
    Stamps 'original_label' for coverage. Other labels (You) are untouched.
    """
    out = []
    for entry in entries:
        result = dict(entry)
        result['original_label'] = entry['label']
        if entry['label'] == caller_label:
            result['label'] = name
        out.append(result)
    return out


def resolve_attribution(entries, spans, lag_seconds, self_name,
                        caller_label='Caller', callers=None):
    """Pick the attribution strategy and apply it.

    Returns (relabeled_entries, info) where info has:
      mode: 'collapse' | 'timed'
      speaker: the single other speaker when collapsed, else None
      others: the distinct named non-self speakers detected
      warning: optional string when a requested mode could not be honored

    Collapse (all Caller -> the one other speaker) fires when there is exactly
    one distinct named non-self speaker AND the caller count isn't declared as a
    group. `callers` overrides auto-detection: callers==1 forces collapse,
    callers>=2 forces timed.
    """
    others = distinct_named_speakers(spans, self_name)
    info = {'mode': 'timed', 'speaker': None, 'others': others, 'warning': None}

    if callers is not None and callers >= 2:
        pass  # group call declared -> timed
    elif callers == 1 or (callers is None and len(others) == 1):
        if len(others) == 1:
            speaker = others[0]
            info.update(mode='collapse', speaker=speaker)
            return collapse_caller(entries, speaker, caller_label), info
        if callers == 1:
            info['warning'] = (
                f'--callers 1 but {len(others)} named speakers detected '
                f'({others or "none"}); using timed attribution'
            )

    return relabel_transcript(entries, spans, lag_seconds, self_name, caller_label), info


def render_transcript(entries):
    """Render entries back to '[h:mm:ss] [Label] text\\n' lines."""
    lines = []
    for entry in entries:
        h, rem = divmod(entry['t'], 3600)
        mn, sec = divmod(rem, 60)
        lines.append(f"[{h:01d}:{mn:02d}:{sec:02d}] [{entry['label']}] {entry['text']}\n")
    return ''.join(lines)
