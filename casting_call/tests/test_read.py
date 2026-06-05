from casting_call.roster import Roster
from casting_call.read import read_active_speaker

ROSTER = Roster(
    self_name='Sam Gutentag',
    members=[{'canonical': 'Dana Park', 'aliases': ['D Park', 'Dana']}],
)

CAPTION_LINES = [
    'grids',
    'You',
    'Yeah.',
    'Dana Park',
    'so what about the sidebar layout here?',
    'You',
    "Yeah, that part makes sense. Yeah.",
]


def test_returns_bottom_most_speaker():
    name, raw = read_active_speaker(CAPTION_LINES, ROSTER, 0.72)
    assert name == 'Sam Gutentag'
    assert raw == 'You'


def test_picks_other_speaker_when_they_are_last():
    lines = ['You', 'Yeah.', 'Dana Park', 'so what about the sidebar layout?']
    name, raw = read_active_speaker(lines, ROSTER, 0.72)
    assert name == 'Dana Park'


def test_unknown_name_is_unresolved_not_occlusion():
    lines = ['You', 'hello', 'Jrdn Mendez', 'whats up everyone']
    name, raw = read_active_speaker(lines, ROSTER, 0.72)
    assert name is None
    assert raw == 'Jrdn Mendez'


def test_non_caption_text_is_occlusion():
    lines = [
        'def relabel_transcript(lines, spans, lag_seconds):',
        '    for entry in lines:',
        '        t = entry["t"] - lag_seconds',
    ]
    name, raw = read_active_speaker(lines, ROSTER, 0.72)
    assert name is None
    assert raw is None


def test_empty_is_occlusion():
    assert read_active_speaker([], ROSTER, 0.72) == (None, None)
