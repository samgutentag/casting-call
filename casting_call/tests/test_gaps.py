from casting_call.gaps import format_windows, merge_windows, transcript_gaps


def _lines(*pairs):
    return [{'t': t, 'label': label, 'text': 'x'} for t, label in pairs]


def test_a_far_side_that_stops_talking_leaves_a_gap_to_the_end_of_the_call():
    entries = _lines((10, 'Caller'), (40, 'Caller'), (498, 'Caller'), (520, 'You'), (2100, 'You'))
    assert transcript_gaps(entries, 'Caller', 120, 2139) == [(50.0, 498.0), (508.0, 2139.0)]


def test_short_pauses_are_not_gaps():
    entries = _lines((0, 'Caller'), (60, 'Caller'), (150, 'Caller'))
    assert transcript_gaps(entries, 'Caller', 120, 200) == []


def test_a_channel_with_no_lines_is_one_gap():
    assert transcript_gaps(_lines((5, 'You')), 'Caller', 120, 600) == [(0.0, 600.0)]


def test_merge_joins_overlaps_and_keeps_separate_spans_apart():
    got = merge_windows([(495.0, 2139.0)], [(600.0, 700.0), (100.0, 200.0)])
    assert got == [(100.0, 200.0), (495.0, 2139.0)]
    assert format_windows(got) == '100.000:200.000,495.000:2139.000'
