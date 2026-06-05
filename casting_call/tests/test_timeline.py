from casting_call.timeline import Span, build_timeline

FPS = 2.0  # 0.5s per frame


def obs(*triples):
    return [(i, n, r) for i, n, r in triples]


def test_collapses_consecutive_same_speaker():
    observations = obs(
        (0, 'Sam Gutentag', 'You'),
        (1, 'Sam Gutentag', 'You'),
        (2, 'Sam Gutentag', 'You'),
        (3, 'Dana Park', 'Dana Park'),
        (4, 'Dana Park', 'Dana Park'),
    )
    spans = build_timeline(observations, fps=FPS, debounce_seconds=1.0, grace_bridge_seconds=3.0)
    assert len(spans) == 2
    assert spans[0].name == 'Sam Gutentag'
    assert spans[0].start == 0.0
    assert spans[0].end == 1.5
    assert spans[1].name == 'Dana Park'
    assert spans[1].start == 1.5


def test_drops_subsecond_flicker():
    observations = obs(
        (0, 'Sam Gutentag', 'You'),
        (1, 'Sam Gutentag', 'You'),
        (2, 'Dana Park', 'Dana Park'),  # single-frame flicker
        (3, 'Sam Gutentag', 'You'),
        (4, 'Sam Gutentag', 'You'),
    )
    spans = build_timeline(observations, fps=FPS, debounce_seconds=1.0, grace_bridge_seconds=3.0)
    assert [s.name for s in spans] == ['Sam Gutentag']


def test_grace_bridges_brief_gap_same_speaker():
    observations = obs(
        (0, 'Dana Park', 'Dana Park'),
        (1, 'Dana Park', 'Dana Park'),
        (2, None, None),
        (3, None, None),  # 1.0s gap < 3.0s grace
        (4, 'Dana Park', 'Dana Park'),
        (5, 'Dana Park', 'Dana Park'),
    )
    spans = build_timeline(observations, fps=FPS, debounce_seconds=1.0, grace_bridge_seconds=3.0)
    real = [s for s in spans if s.name == 'Dana Park']
    assert len(real) == 1
    assert real[0].start == 0.0
    assert real[0].end == 3.0


def test_long_gap_not_bridged():
    observations = obs(
        (0, 'Dana Park', 'Dana Park'),
        (1, 'Dana Park', 'Dana Park'),
        (2, None, None), (3, None, None), (4, None, None),
        (5, None, None), (6, None, None), (7, None, None),  # 3.0s gap >= grace
        (8, 'Dana Park', 'Dana Park'),
        (9, 'Dana Park', 'Dana Park'),
    )
    spans = build_timeline(observations, fps=FPS, debounce_seconds=1.0, grace_bridge_seconds=3.0)
    real = [s for s in spans if s.name == 'Dana Park']
    assert len(real) == 2


def test_unresolved_preserved():
    observations = obs(
        (0, None, 'Jrdn Mendez'),
        (1, None, 'Jrdn Mendez'),
    )
    spans = build_timeline(observations, fps=FPS, debounce_seconds=0.5, grace_bridge_seconds=3.0)
    assert len(spans) == 1
    assert spans[0].name is None
    assert spans[0].unresolved is True
    assert spans[0].raw_ocr == 'Jrdn Mendez'


def test_debounce_does_not_inflate_gap_suppressing_grace_bridge():
    # Bug regression: a sub-debounce flicker of the same speaker immediately after a gap
    # was being absorbed into the gap's run, inflating its duration past grace_bridge_seconds
    # and preventing a valid bridge.
    #
    # Layout (FPS=2.0, 0.5s/frame, debounce=1.0s, grace=3.0s):
    #   frames 0-1  : Sam (1.0s)
    #   frames 2-6  : gap (true duration 2.5s, start=1.0 end=3.5 → < 3.0s grace, should bridge)
    #   frame  7    : Sam flicker (0.5s, sub-debounce, flanked by gaps on both sides)
    #   frames 8-9  : gap (1.0s)
    #   frames 10-11: Sam (1.0s)
    #
    # Without the fix: frame-7 flicker is piggybacked onto the preceding gap, inflating
    # its end from 3.5 to 4.0 (duration 3.0).  3.0 is NOT < 3.0 grace, so the bridge
    # is suppressed and two separate Sam spans are emitted.
    # After the fix: flicker is appended as its own entry; both gaps remain < grace and
    # the whole sequence collapses to one Sam span (0.0–6.0).
    observations = obs(
        (0, 'Sam Gutentag', 'You'),
        (1, 'Sam Gutentag', 'You'),
        (2, None, None),
        (3, None, None),
        (4, None, None),
        (5, None, None),
        (6, None, None),
        (7, 'Sam Gutentag', 'You'),  # sub-debounce flicker (0.5s < 1.0s), gap on both sides
        (8, None, None),
        (9, None, None),
        (10, 'Sam Gutentag', 'You'),
        (11, 'Sam Gutentag', 'You'),
    )
    spans = build_timeline(observations, fps=FPS, debounce_seconds=1.0, grace_bridge_seconds=3.0)
    sam_spans = [s for s in spans if s.name == 'Sam Gutentag']
    assert len(sam_spans) == 1, (
        f'Expected 1 Sam span after grace-bridge, got {len(sam_spans)}: {sam_spans}'
    )
    assert sam_spans[0].start == 0.0
    assert sam_spans[0].end == 6.0


def test_empty_observations_returns_empty():
    spans = build_timeline([], fps=2.0, debounce_seconds=1.0, grace_bridge_seconds=3.0)
    assert spans == []


def test_grace_bridge_does_not_fire_across_different_speakers():
    # A short gap flanked by different speakers must NOT be bridged.
    observations = obs(
        (0, 'Sam Gutentag', 'You'),
        (1, 'Sam Gutentag', 'You'),
        (2, None, None),
        (3, None, None),  # 1.0s gap < 3.0s grace, but different speakers on either side
        (4, 'Dana Park', 'Dana Park'),
        (5, 'Dana Park', 'Dana Park'),
    )
    spans = build_timeline(observations, fps=FPS, debounce_seconds=1.0, grace_bridge_seconds=3.0)
    names = [s.name for s in spans]
    assert 'Sam Gutentag' in names
    assert 'Dana Park' in names
    assert len(spans) == 2
