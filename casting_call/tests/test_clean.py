from casting_call.transcript import (
    parse_transcript, strip_junk_lines, strip_silent_lines, collapse_repeats,
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


def test_collapse_repeats_drops_the_whole_loop_run():
    # A long run of an identical line is whisper looping on dead audio. Keeping a
    # representative leaves a sentence in the transcript that was never spoken,
    # which is how one "Thank you." survived a 54-line loop on a real call.
    entries = parse_transcript(
        "[0:30:08] [You] I hope you enjoyed this video.\n"
        "[0:30:12] [You] I hope you enjoyed this video.\n"
        "[0:30:16] [You] I hope you enjoyed this video.\n"
        "[0:30:20] [You] okay next thing\n"
    )
    out = collapse_repeats(entries)
    assert [e['text'] for e in out] == ['okay next thing']


def test_short_repeat_still_collapses_to_one():
    # Two in a row is a person repeating themselves, or a merge artifact. Keep one.
    entries = parse_transcript(
        "[0:10:00] [You] say that again please\n"
        "[0:10:03] [You] say that again please\n"
    )
    assert [e['text'] for e in collapse_repeats(entries)] == ['say that again please']


def test_loop_run_threshold_is_configurable():
    entries = parse_transcript(
        "[0:10:00] [You] the same sentence\n"
        "[0:10:03] [You] the same sentence\n"
    )
    assert collapse_repeats(entries, drop_run_min=2) == []


def test_a_long_thank_you_loop_is_removed_entirely():
    # The exact shape from the 2026-08-28 eng-ama call.
    text = "".join(f"[0:27:49] [You] Thank you.\n" for _ in range(54))
    entries = parse_transcript("[0:27:00] [Caller] real speech\n" + text)
    out = collapse_repeats(entries)
    assert [e['text'] for e in out] == ['real speech']


def test_loop_run_ends_at_a_different_line():
    entries = parse_transcript(
        "[0:01:00] [You] looped line here\n"
        "[0:01:02] [You] looped line here\n"
        "[0:01:04] [You] looped line here\n"
        "[0:01:06] [You] genuine speech\n"
        "[0:01:08] [You] looped line here\n"
    )
    out = collapse_repeats(entries)
    # the run goes; the lone later occurrence is not a run and survives
    assert [e['text'] for e in out] == ['genuine speech', 'looped line here']


def test_collapse_keeps_single_word_backchannel():
    # real 'Yeah. Yeah.' is single-word, must survive
    entries = parse_transcript("[0:00:01] [You] Yeah.\n[0:00:02] [You] Yeah.\n")
    assert len(collapse_repeats(entries)) == 2


def test_collapse_only_within_same_speaker():
    entries = parse_transcript(
        "[0:00:01] [You] same words here\n"
        "[0:00:02] [Caller] same words here\n"
    )
    # different speakers saying the same thing is not a loop
    assert len(collapse_repeats(entries)) == 2
