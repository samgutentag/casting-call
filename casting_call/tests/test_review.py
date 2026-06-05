from casting_call.timeline import Span
from casting_call.review import collect_unknowns, reconcile


def make_spans():
    return [
        Span(0.0, 5.0, 'Sam Gutentag', 'You'),
        Span(5.0, 9.0, None, 'Jrdn Mendez', unresolved=True),
        Span(9.0, 12.0, None, 'Cassie R', unresolved=True),
        Span(12.0, 15.0, None, 'Jrdn Mendez', unresolved=True),
    ]


def test_collect_distinct_unknowns_assigns_review_ids():
    spans = make_spans()
    unknowns = collect_unknowns(spans)
    assert len(unknowns) == 2
    assert unknowns[0] == {'review_id': 'unknown_01', 'raw_ocr': 'Jrdn Mendez'}
    assert unknowns[1]['review_id'] == 'unknown_02'
    ids = {s.review_id for s in spans if s.raw_ocr == 'Jrdn Mendez'}
    assert ids == {'unknown_01'}


def test_reconcile_applies_mapping():
    spans = make_spans()
    collect_unknowns(spans)
    out = reconcile(spans, {'unknown_01': 'Jordan Mendez', 'unknown_02': 'Cassie Reyes'})
    by_time = {s.start: s.name for s in out}
    assert by_time[5.0] == 'Jordan Mendez'
    assert by_time[9.0] == 'Cassie Reyes'
    assert by_time[12.0] == 'Jordan Mendez'
    assert all(not s.unresolved for s in out)


def test_reconcile_bail_collapses_rest_to_caller():
    spans = make_spans()
    collect_unknowns(spans)
    out = reconcile(spans, {'unknown_01': 'Jordan Mendez'}, bail=True)
    by_time = {s.start: s for s in out}
    assert by_time[5.0].name == 'Jordan Mendez'
    assert by_time[9.0].name is None
    assert by_time[9.0].unresolved is False
    assert by_time[12.0].name == 'Jordan Mendez'


def test_reconcile_partial_no_bail_leaves_unmapped_pending():
    spans = make_spans()
    collect_unknowns(spans)
    out = reconcile(spans, {'unknown_01': 'Jordan Mendez'}, bail=False)
    by_time = {s.start: s for s in out}
    assert by_time[9.0].unresolved is True
