from casting_call.roster import Roster
from casting_call.stitch import (
    dedupe_stutter, is_gibberish, merge, speaker_of,
)

ROSTER = Roster(self_name='Sam', members=[
    {'canonical': 'Sarah', 'aliases': ['Sarah Deaton']},
])


def test_speaker_of_maps_you_to_self():
    assert speaker_of('You', ROSTER) == 'Sam'


def test_speaker_of_fuzzy_matches_ocr_misreads():
    assert speaker_of('Sarah Deaton', ROSTER) == 'Sarah'
    assert speaker_of('Sarah Deston |', ROSTER) == 'Sarah'


def test_speaker_of_rejects_caption_text():
    assert speaker_of('so I said to Sarah that the docs were fine', ROSTER) is None
    assert speaker_of('yeah totally', ROSTER) is None


def test_merge_appends_only_new_tail():
    # overlap must reach ANCHOR (4) tokens for the alignment to be trusted
    committed = ['SPK1', 'the', 'quick', 'brown', 'fox', 'jumps']
    frame = ['quick', 'brown', 'fox', 'jumps', 'over', 'the', 'lazy', 'dog']
    out = merge(committed, frame)
    assert out == ['SPK1', 'the', 'quick', 'brown', 'fox', 'jumps',
                   'over', 'the', 'lazy', 'dog']


def test_merge_below_anchor_appends_whole_frame():
    committed = ['a', 'b', 'c']
    frame = ['c', 'x', 'y']          # 1-token overlap: not trustworthy
    assert merge(committed, frame) == ['a', 'b', 'c', 'c', 'x', 'y']


def test_merge_survives_interim_revision():
    # frame N had an interim reading; frame N+1 revised a word mid-overlap
    committed = ['we', 'should', 'ship', 'the', 'docs', 'page', 'now']
    frame = ['the', 'docs', 'page', 'now', 'and', 'then', 'iterate']
    out = merge(committed, frame)
    assert out[-3:] == ['and', 'then', 'iterate']
    assert out.count('docs') == 1


def test_dedupe_stutter_collapses_repeats():
    toks = 'like Yeah I like Yeah I think'.split()
    assert dedupe_stutter(toks) == 'like Yeah I think'.split()


def test_gibberish_filter():
    assert is_gibberish('yUU UUl PIUUULLS OU')
    assert not is_gibberish('this is a normal caption line')
