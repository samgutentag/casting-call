from casting_call.roster import Roster, match_name

ROSTER = Roster(
    self_name='Sam Gutentag',
    members=[{'canonical': 'Dana Park', 'aliases': ['D Park', 'Dana']}],
)


def test_you_maps_to_self():
    assert match_name('You', ROSTER, 0.72) == 'Sam Gutentag'
    assert match_name('you', ROSTER, 0.72) == 'Sam Gutentag'


def test_exact_canonical_match():
    assert match_name('Dana Park', ROSTER, 0.72) == 'Dana Park'


def test_garbled_ocr_snaps_to_roster():
    assert match_name('D4na Park', ROSTER, 0.72) == 'Dana Park'


def test_alias_match():
    assert match_name('Dana', ROSTER, 0.72) == 'Dana Park'


def test_unknown_returns_none():
    assert match_name('Jordan Mendez', ROSTER, 0.72) is None
