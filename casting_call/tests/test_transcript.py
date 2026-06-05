from casting_call.timeline import Span
from casting_call.transcript import (
    parse_transcript, relabel_transcript, render_transcript,
    distinct_named_speakers, collapse_caller, resolve_attribution,
)

SPANS = [
    Span(0.0, 10.0, 'Sam Gutentag', 'You'),
    Span(10.0, 20.0, 'Dana Park', 'Dana Park'),
    Span(20.0, 30.0, None, 'Jrdn', unresolved=True),
]

TRANSCRIPT = (
    "[0:00:03] [You] hey\n"
    "[0:00:12] [Caller] some more?\n"        # 12s -1.5 lag = 10.5 -> Dana
    "[0:00:05] [Caller] uh huh\n"            # 5s span is Sam -> bleed -> Caller
    "[0:00:25] [Caller] hello\n"             # unresolved span -> Caller
)


def test_parse_transcript():
    entries = parse_transcript(TRANSCRIPT)
    assert entries[0] == {'t': 3, 'label': 'You', 'text': 'hey'}
    assert entries[1] == {'t': 12, 'label': 'Caller', 'text': 'some more?'}


def test_relabel_caller_by_timeline():
    entries = parse_transcript(TRANSCRIPT)
    out = relabel_transcript(entries, SPANS, lag_seconds=1.5,
                             self_name='Sam Gutentag', caller_label='Caller')
    assert [e['label'] for e in out] == ['You', 'Dana Park', 'Caller', 'Caller']


def test_you_lines_untouched():
    entries = parse_transcript("[0:00:03] [You] hey\n")
    out = relabel_transcript(entries, SPANS, lag_seconds=1.5,
                             self_name='Sam Gutentag', caller_label='Caller')
    assert out[0]['label'] == 'You'


def test_off_timeline_stays_caller():
    entries = parse_transcript("[0:01:05] [Caller] hello there\n")  # 65s, off the end
    out = relabel_transcript(entries, SPANS, lag_seconds=0.0,
                             self_name='Sam Gutentag', caller_label='Caller')
    assert render_transcript(out) == "[0:01:05] [Caller] hello there\n"


def test_render_uses_resolved_name():
    entries = parse_transcript("[0:00:12] [Caller] some more?\n")
    out = relabel_transcript(entries, SPANS, lag_seconds=1.5,
                             self_name='Sam Gutentag', caller_label='Caller')
    assert render_transcript(out) == "[0:00:12] [Dana Park] some more?\n"


# --- single-caller collapse (2-person calls) ---

ONE_OTHER = [
    Span(0.0, 10.0, 'Sam Gutentag', 'You'),
    Span(10.0, 20.0, 'Dana Park', 'Dana Park'),
    Span(20.0, 30.0, None, "It's like four others", unresolved=True),  # OCR noise
]

TWO_OTHERS = [
    Span(0.0, 10.0, 'Dana Park', 'Dana Park'),
    Span(10.0, 20.0, 'Jordan Mendez', 'Jordan Mendez'),
]

CALLER_LINES = (
    "[0:00:03] [You] hey\n"
    "[0:00:25] [Caller] first\n"     # in the noise/unresolved span -> would stay Caller if timed
    "[0:00:12] [Caller] second\n"
)


def test_distinct_named_excludes_self_and_noise():
    assert distinct_named_speakers(ONE_OTHER, 'Sam Gutentag') == ['Dana Park']
    assert distinct_named_speakers(TWO_OTHERS, 'Sam Gutentag') == ['Dana Park', 'Jordan Mendez']


def test_collapse_relabels_every_caller_line():
    entries = parse_transcript(CALLER_LINES)
    out = collapse_caller(entries, 'Dana Park', 'Caller')
    labels = [e['label'] for e in out]
    assert labels == ['You', 'Dana Park', 'Dana Park']
    assert all(e['original_label'] for e in out)


def test_resolve_auto_collapses_one_other_speaker():
    entries = parse_transcript(CALLER_LINES)
    out, info = resolve_attribution(entries, ONE_OTHER, 1.5, 'Sam Gutentag')
    assert info['mode'] == 'collapse'
    assert info['speaker'] == 'Dana Park'
    # every Caller line attributed, including the one in the noise span timing would miss
    assert [e['label'] for e in out] == ['You', 'Dana Park', 'Dana Park']


def test_resolve_auto_timed_when_two_others():
    entries = parse_transcript(CALLER_LINES)
    out, info = resolve_attribution(entries, TWO_OTHERS, 1.5, 'Sam Gutentag')
    assert info['mode'] == 'timed'
    assert info['speaker'] is None


def test_resolve_callers_override_forces_timed_on_two_person():
    entries = parse_transcript(CALLER_LINES)
    out, info = resolve_attribution(entries, ONE_OTHER, 1.5, 'Sam Gutentag', callers=2)
    assert info['mode'] == 'timed'  # group declared, no collapse despite one detected


def test_resolve_callers_one_but_ambiguous_warns_and_uses_timed():
    entries = parse_transcript(CALLER_LINES)
    out, info = resolve_attribution(entries, TWO_OTHERS, 1.5, 'Sam Gutentag', callers=1)
    assert info['mode'] == 'timed'
    assert info['warning'] is not None
