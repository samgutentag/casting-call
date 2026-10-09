import numpy as np
from PIL import Image, ImageDraw

from casting_call.backup import (
    caption_lines, frame_tokens, other_words, parse_windows, splice, stitch_timed,
)
from casting_call.locate import HANGUP_RGB, caption_search_box, find_hangup
from casting_call.roster import Roster
from casting_call.stitch import dedupe_stutter, new_tail_start

ROSTER = Roster(self_name='Sam Gutentag', members=[
    {'canonical': 'Corey Weathers', 'aliases': ['Corey']},
])

TSV_HEADER = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext'


def _tsv(rows):
    """rows: [(line_id, [(left, top, w, h, text), ...])] -> tesseract TSV text."""
    out = [TSV_HEADER]
    for line_id, words in rows:
        for i, (left, top, w, h, text) in enumerate(words, 1):
            out.append(f'5\t1\t1\t1\t{line_id}\t{i}\t{left}\t{top}\t{w}\t{h}\t96\t{text}')
    return '\n'.join(out)


def _body(left, top, text):
    words, x = [], left
    for word in text.split():
        words.append((x, top, 18 * len(word), 28, word))
        x += 18 * len(word) + 15
    return words


# --- locating Meet ---------------------------------------------------------

def _frame(pills):
    img = Image.new('RGB', (3840, 2160), (19, 19, 20))
    draw = ImageDraw.Draw(img)
    for cx, cy in pills:
        draw.rounded_rectangle((cx - 54, cy - 34, cx + 54, cy + 34), radius=34, fill=HANGUP_RGB)
    return img


def test_find_hangup_locates_the_end_call_button_wherever_the_window_is():
    for cx, cy in ((3268, 2092), (1648, 1614)):
        found = find_hangup(_frame([(cx, cy)]))
        assert found and abs(found[0] - cx) <= 8 and abs(found[1] - cy) <= 8


def test_find_hangup_refuses_to_guess():
    assert find_hangup(_frame([])) is None                        # Meet hidden
    assert find_hangup(_frame([(1000, 1000), (3000, 1500)])) is None


def test_find_hangup_ignores_a_large_red_tile():
    img = _frame([(3268, 2092)])
    ImageDraw.Draw(img).rectangle((400, 400, 1100, 1000), fill=HANGUP_RGB)
    assert abs(find_hangup(img)[0] - 3268) <= 8


def test_search_box_sits_above_the_button_and_stays_in_frame():
    img = _frame([(3268, 2092)])
    x0, y0, x1, y1 = caption_search_box(img, find_hangup(img))
    assert 0 <= x0 < 3268 < x1 <= 3840
    assert y1 < 2092 and y0 < y1


# --- picking the caption column out of a wide crop ------------------------

def test_caption_lines_keep_the_caption_column_and_drop_everything_else():
    rows = [
        (1, [(400, 10, 90, 20, 'Hila'), (500, 10, 60, 20, 'Qu')]),        # tile label, off the edge
        (2, [(700, 100, 120, 22, 'Corey'), (830, 100, 150, 22, 'Weathers')]),
        (3, _body(700, 140, 'we should ship the docs first') +
            [(1800, 140, 150, 17, 'sounds'), (1960, 140, 90, 17, 'good')]),  # chat bubble, same row
        (4, _body(700, 180, 'and then the blog post')),
        (5, [(630, 220, 40, 50, '@'), (700, 230, 60, 18, 'You')]),           # avatar in front
        (6, _body(700, 270, 'sounds right to me')),
    ]
    gray = np.full((400, 2200), 19, dtype=np.uint8)
    for _, words in rows:
        for left, top, w, h, text in words:
            grey_name = text in ('Corey', 'Weathers', 'You')
            gray[top:top + h, left:left + w] = 211 if grey_name else 255
    gray[140:157, 1800:2050] = 230                                          # chat is dimmer

    lines = caption_lines(_tsv(rows), gray=gray)
    assert lines == [
        ('Corey Weathers', True),
        ('we should ship the docs first', False),
        ('and then the blog post', False),
        ('You', True),
        ('sounds right to me', False),
    ]


def test_a_short_reply_with_no_text_under_it_is_not_a_speaker():
    rows = [(1, _body(700, 100, 'we can do that')), (2, [(700, 140, 60, 20, 'Yes.')])]
    gray = np.full((300, 1200), 255, dtype=np.uint8)
    gray[140:160, 700:760] = 211
    assert caption_lines(_tsv(rows), gray=gray)[-1] == ('Yes.', False)


def test_frame_tokens_hold_back_the_interim_bottom_line():
    lines = [('Corey Weathers', True), ('ship it', False), ('and the', False)]
    assert frame_tokens(lines, ROSTER) == [('SPK', 'Corey Weathers'), 'ship', 'it']
    assert frame_tokens(lines, ROSTER, keep_last=True)[-2:] == ['and', 'the']


def test_you_maps_to_sam():
    assert frame_tokens([('You', True), ('hi', False)], ROSTER, keep_last=True)[0] == \
        ('SPK', 'Sam Gutentag')


# --- timing ----------------------------------------------------------------

def test_stitch_timed_stamps_each_line_with_when_it_first_appeared():
    spk = ('SPK', 'Corey Weathers')
    frames = [
        (100.0, [spk, 'we', 'should', 'ship', 'the', 'docs']),
        (100.7, [spk, 'we', 'should', 'ship', 'the', 'docs', 'first.']),
        (110.0, ['should', 'ship', 'the', 'docs', 'first.', ('SPK', 'Sam Gutentag'), 'agreed.']),
    ]
    lines = stitch_timed(frames, ROSTER)
    assert lines == [
        (100.0, 'Corey Weathers', 'we should ship the docs first.'),
        (110.0, 'Sam Gutentag', 'agreed.'),
    ]


def test_long_turns_split_into_transcript_sized_lines():
    spk = ('SPK', 'Corey Weathers')
    first = [spk, 'one', 'two', 'three', 'four.']
    frames = [(0.0, first), (7.0, first + ['five', 'six.'])]
    lines = stitch_timed(frames, ROSTER)
    assert [t for t, _, _ in lines] == [0.0, 7.0]


def test_new_tail_start_and_keyed_dedupe():
    assert new_tail_start(['a', 'b', 'c', 'd', 'e'], ['b', 'c', 'd', 'e', 'f']) == 4
    assert new_tail_start([], ['x']) == 0
    pairs = [('like', 1), ('yeah', 1), ('like', 2), ('yeah', 2), ('ok', 3)]
    assert dedupe_stutter(pairs, key=lambda p: p[0]) == [('like', 2), ('yeah', 2), ('ok', 3)]


# --- deciding and splicing -------------------------------------------------

def test_other_words_ignores_sam():
    lines = [(1, 'Sam Gutentag', 'a b c'), (2, 'Corey Weathers', 'd e')]
    assert other_words(lines, 'Sam Gutentag') == 2


def test_splice_replaces_only_far_side_lines_inside_the_window():
    entries = [
        {'t': 50, 'label': 'Caller', 'text': 'before the drop'},
        {'t': 120, 'label': 'You', 'text': 'my own words'},
        {'t': 130, 'label': 'Caller', 'text': 'whisper on silence'},
        {'t': 400, 'label': 'Caller', 'text': 'after the drop'},
    ]
    captions = [
        (121.5, 'Corey Weathers', 'captioned far side'),
        (125.0, 'Sam Gutentag', 'my own words again'),
        (90.0, 'Corey Weathers', 'outside the window'),
    ]
    out, added = splice(entries, [(100, 300)], captions, 'Sam Gutentag', lag=1.5)
    assert added == 1
    assert [(e['t'], e['label'], e['text']) for e in out] == [
        (50, 'Caller', 'before the drop'),
        (120, 'You', 'my own words'),
        (120, 'Caller', 'captioned far side'),
        (400, 'Caller', 'after the drop'),
    ]


def test_parse_windows():
    assert parse_windows('312.5:871,1500:1620') == [(312.5, 871.0), (1500.0, 1620.0)]
    assert parse_windows('') == []
