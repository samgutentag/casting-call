from casting_call.coverage import coverage_report


def entry(t, original_label, label):
    return {'t': t, 'original_label': original_label, 'label': label, 'text': 'x'}


def test_attributed_vs_fallback_counts():
    entries = [
        entry(10, 'Caller', 'Dana Park'),
        entry(20, 'Caller', 'Caller'),
        entry(30, 'You', 'You'),  # not a caller line; ignored
    ]
    report = coverage_report(entries, caller_label='Caller')
    assert report['caller_lines'] == 2
    assert report['attributed_lines'] == 1
    assert report['attributed_pct'] == 50.0
    assert report['lost_lines'] == [{'t': 20, 'text': 'x'}]


def test_all_attributed():
    entries = [entry(5, 'Caller', 'Dana Park')]
    report = coverage_report(entries, caller_label='Caller')
    assert report['attributed_pct'] == 100.0
    assert report['lost_lines'] == []


def test_no_caller_lines_is_zero_safe():
    entries = [entry(5, 'You', 'You')]
    report = coverage_report(entries, caller_label='Caller')
    assert report['caller_lines'] == 0
    assert report['attributed_pct'] == 0.0
