from casting_call.transcript import (
    parse_transcript, strip_junk_lines, strip_silent_lines,
)


def test_strip_junk_removes_amara_hallucination():
    entries = parse_transcript(
        "[0:00:05] [Caller] real words here\n"
        "[0:34:40] [Caller] Subtitles by the Amara.org community\n"
    )
    assert [e['text'] for e in strip_junk_lines(entries)] == ['real words here']


def test_strip_junk_keeps_a_real_thank_you():
    # "thank you" is common real speech and is NOT in the junk set; the silence-span
    # filter is what removes hallucinated ones (they sit in dead audio).
    entries = parse_transcript("[0:00:05] [Caller] Thank you.\n")
    assert len(strip_junk_lines(entries)) == 1


def test_strip_silent_lines_drops_only_that_labels_span():
    entries = parse_transcript(
        "[0:14:00] [Caller] last real caller line\n"   # 840s, before dropout
        "[0:20:00] [Caller] Thank you.\n"               # 1200s, inside caller dropout -> junk
        "[0:20:00] [You] I am still talking\n"          # You track fine -> keep
    )
    spans = {'Caller': [(872.0, 2085.0)]}   # far-side dropout 14:32 -> end
    out = strip_silent_lines(entries, spans)
    kept = [(e['label'], e['text']) for e in out]
    assert ('Caller', 'last real caller line') in kept
    assert ('You', 'I am still talking') in kept
    assert ('Caller', 'Thank you.') not in kept


def test_strip_silent_lines_no_spans_is_noop():
    entries = parse_transcript("[0:00:05] [You] hi\n[0:00:10] [Caller] hey\n")
    assert strip_silent_lines(entries, {}) == entries
