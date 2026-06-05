from dataclasses import dataclass


@dataclass
class Span:
    start: float
    end: float
    name: str | None
    raw_ocr: str | None = None
    confidence: float = 1.0
    unresolved: bool = False
    review_id: str | None = None


def _key(name, raw):
    if name is not None:
        return ('name', name)
    if raw is not None:
        return ('unknown', raw)
    return ('gap', None)


def _raw_runs(observations, fps):
    runs = []
    for frame_index, name, raw in observations:
        t = frame_index / fps
        k = _key(name, raw)
        if runs and runs[-1][3] == k:
            runs[-1][1] = t + (1.0 / fps)
        else:
            runs.append([t, t + (1.0 / fps), (name, raw), k])
    return runs


def build_timeline(observations, fps, debounce_seconds, grace_bridge_seconds):
    runs = _raw_runs(observations, fps)

    # Drop sub-debounce non-gap flickers by merging into the previous run.
    cleaned = []
    for run in runs:
        start, end, payload, k = run
        if k[0] != 'gap' and (end - start) < debounce_seconds and cleaned and cleaned[-1][3][0] != 'gap':
            cleaned[-1][1] = end
            continue
        cleaned.append(run)

    # Collapse adjacent runs with the same key that may result from flicker removal.
    merged = []
    for run in cleaned:
        if merged and merged[-1][3] == run[3]:
            merged[-1][1] = run[1]
        else:
            merged.append(run)
    cleaned = merged

    # Grace-bridge: a short gap flanked by the same named speaker is absorbed.
    bridged = []
    i = 0
    while i < len(cleaned):
        run = cleaned[i]
        if (
            run[3][0] == 'gap'
            and bridged
            and i + 1 < len(cleaned)
            and (run[1] - run[0]) < grace_bridge_seconds
            and bridged[-1][3] == cleaned[i + 1][3]
            and bridged[-1][3][0] == 'name'
        ):
            bridged[-1][1] = cleaned[i + 1][1]
            i += 2
            continue
        bridged.append(run)
        i += 1

    spans = []
    for start, end, (name, raw), k in bridged:
        if k[0] == 'gap':
            continue  # gaps are not emitted; relabel falls back to Caller
        spans.append(Span(
            start=round(start, 3), end=round(end, 3),
            name=name, raw_ocr=raw, unresolved=(name is None),
        ))
    return spans
