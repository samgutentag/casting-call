import json
from collections import Counter

from casting_call import names
from casting_call.digest import window_for
from casting_call.names import (
    caption_words, find_people, fold_names, line_spans, name_lines, pick, similarity,
)
from casting_call.transcript import is_far_side, parse_transcript


# --- folding spellings ------------------------------------------------------

def test_similarity_word_subset_substring_and_ratio():
    assert similarity('Corey', 'Corey Weathers') == 1.0
    assert similarity('orey Weathers', 'Corey Weathers') == 0.95
    assert similarity('Ed Charbeneav', 'Ed Charbeneau') >= 0.8
    assert similarity('Naomi Carrigan', 'Ed Charbeneau') < 0.8


def test_fold_names_into_most_common_spelling():
    canon = fold_names({'Corey Weathers': 40, 'Corey': 3, 'orey Weathers': 2,
                        'Ed Charbeneau': 30, 'Ed Charbeneav': 4})
    assert canon['Corey'] == canon['orey Weathers'] == 'Corey Weathers'
    assert canon['Ed Charbeneav'] == 'Ed Charbeneau'
    assert canon['Ed Charbeneau'] == 'Ed Charbeneau'


def _frame(t, *lines):
    return (t, list(lines), True)


def test_find_people_drops_one_frame_names_and_excludes_you():
    frames = [
        _frame(0, ('Corey Weathers', True), ('hello there', False)),
        _frame(2, ('Corey Weathers', True), ('hello there friends', False)),
        _frame(4, ('You', True), ('hi', False)),
        _frame(6, ('You', True), ('hi all', False)),
        _frame(8, ('Thanks so much', True), ('ok', False)),   # misread caption line
    ]
    canon, people, spellings = find_people(frames)
    assert people == ['Corey Weathers']
    assert canon['You'] == 'You'
    assert 'Thanks so much' not in people
    assert spellings == {'Corey Weathers': {'Corey Weathers': 2}}


def test_find_people_counts_frames_not_lines():
    # One frame showing the same label twice is still one frame.
    frames = [_frame(0, ('Ed', True), ('a b', False), ('Ed', True), ('c d', False))]
    assert find_people(frames)[1] == []


def test_find_people_strips_bracket_chars_from_labels():
    frames = [_frame(t, ('| Naomi Carrigan', True), ('x y', False)) for t in (0, 2)]
    assert find_people(frames)[1] == ['Naomi Carrigan']


# --- stitching --------------------------------------------------------------

def test_caption_words_carry_speaker_and_shifted_time():
    frames = [
        _frame(10, ('Ed', True), ('one two three four', False), ('five', False)),
        _frame(12, ('Ed', True), ('one two three four', False), ('five six', False)),
    ]
    canon, people, _ = find_people(frames)
    words = caption_words(frames, canon, people, lag=2.5)
    assert {s for _, s in words} == {'Ed'}
    assert words[0] == (7.5, 'Ed')
    assert len(words) == 6


def test_caption_words_skip_you_label_attribution():
    frames = [
        _frame(10, ('You', True), ('my words here', False), ('Ed', True), ('his words', False)),
        _frame(12, ('You', True), ('my words here', False), ('Ed', True), ('his words now', False)),
    ]
    canon, people, _ = find_people(frames)
    words = caption_words(frames, canon, people, lag=0)
    assert Counter(s for _, s in words) == Counter({'You': 3, 'Ed': 3})


# --- voting -----------------------------------------------------------------

def test_pick_needs_five_words_and_seventy_percent():
    assert pick(Counter({'Ed': 4})) is None
    assert pick(Counter({'Ed': 7, 'Corey': 3})) == 'Ed'
    assert pick(Counter({'Ed': 6, 'Corey': 4})) is None


def test_line_spans_run_to_next_caller_line_capped():
    entries = parse_transcript('[0:00:10] [Caller] a\n[0:00:12] [You] b\n'
                               '[0:00:15] [Caller] c\n[0:01:00] [Caller] d\n')
    assert line_spans(entries) == [(10, 15), (15, 27), (60, 72)]


TRANSCRIPT = ('[0:00:10] [Caller] first line\n'
              '[0:00:14] [You] mine\n'
              '[0:00:20] [Caller] second line\n'
              '[0:00:40] [Caller] third line\n'
              '[0:00:41] [MARKER] marker\n')


def test_name_lines_votes_each_line():
    entries = parse_transcript(TRANSCRIPT)
    words = ([(11 + i * 0.5, 'Ed') for i in range(8)]
             + [(21 + i * 0.5, 'Corey') for i in range(8)]
             + [(41, 'Ed'), (41.5, 'Corey')])
    out, records = name_lines(entries, words, ['Ed', 'Corey'])
    assert [e['label'] for e in out] == ['Ed', 'You', 'Corey', 'Caller', 'MARKER']
    assert records[2]['votes'] == {'Ed': 1, 'Corey': 1} and records[2]['name'] is None


def test_name_lines_single_name_call_labels_every_caller_line():
    entries = parse_transcript(TRANSCRIPT)
    out, _ = name_lines(entries, [], ['Naomi'])
    assert [e['label'] for e in out] == ['Naomi', 'You', 'Naomi', 'Naomi', 'MARKER']


# --- labels downstream ------------------------------------------------------

def test_named_labels_parse_and_count_as_far_side():
    entries = parse_transcript('[0:00:01] [Corey Weathers] hi\n[0:00:02] [You] yo\n'
                               '[0:00:03] [MARKER] marker\n[0:00:04] [Caller] ok\n')
    assert [e['label'] for e in entries] == ['Corey Weathers', 'You', 'MARKER', 'Caller']
    assert [is_far_side(e['label']) for e in entries] == [True, False, False, True]


def test_digest_context_marks_side_for_named_lines():
    speech = parse_transcript('[0:00:01] [Corey Weathers] hi\n[0:00:02] [You] yo\n')
    ctx = window_for({'t': 1}, speech, 30)
    assert [c['side'] for c in ctx] == ['far', 'you']


# --- CLI --------------------------------------------------------------------

def _write_frames(path, frames):
    path.write_text(json.dumps({'frames': [
        {'t': t, 'found': found, 'lines': [list(x) for x in lines]}
        for t, lines, found in frames]}))


def test_main_writes_pre_names_and_json(tmp_path, capsys):
    txt = tmp_path / 't.txt'
    txt.write_text('card header line\n' + TRANSCRIPT)
    frames = [_frame(t, ('Ed Charbeneau', True), ('words ' * 3 + str(t), False))
              for t in range(0, 60, 2)]
    _write_frames(tmp_path / 'frames.json', frames)
    parts = tmp_path / 'parts'
    names.main(['v.mp4', str(txt), '--parts', str(parts),
                '--frames', str(tmp_path / 'frames.json')])
    out = txt.read_text()
    assert out.startswith('card header line\n')
    assert '[Ed Charbeneau] first line' in out and '[Caller]' not in out
    assert (parts / 'pre-names.txt').read_text() == 'card header line\n' + TRANSCRIPT
    data = json.loads((parts / 'speaker-names.json').read_text())
    assert data['single_name_call'] and data['names']['Ed Charbeneau']['lines'] == 3
    assert 'renamed 3/3' in capsys.readouterr().out


def test_main_skips_cleanly_without_meet(tmp_path, capsys):
    txt = tmp_path / 't.txt'
    txt.write_text(TRANSCRIPT)
    _write_frames(tmp_path / 'frames.json', [(t, [], False) for t in range(0, 10, 2)])
    names.main(['v.mp4', str(txt), '--parts', str(tmp_path / 'parts'),
                '--frames', str(tmp_path / 'frames.json')])
    assert 'no Meet on screen' in capsys.readouterr().out
    assert txt.read_text() == TRANSCRIPT
    assert not (tmp_path / 'parts' / 'pre-names.txt').exists()


def test_ocr_variants_of_you_are_self():
    from casting_call.names import is_self_label
    for lab in ("You", "vou", "Yov", "You.", "you"):
        assert is_self_label(lab), lab
    for lab in ("Ed", "Yuki Tanaka", "Corey", "Naomi", "Yui", "Yon"):
        assert not is_self_label(lab), lab
