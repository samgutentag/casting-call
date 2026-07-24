from casting_call.transcript import parse_transcript, render_transcript
from casting_call.markers import match_marker, markers_from_entries, embed_markers


def test_match_marker_finds_keyword_anywhere():
    assert match_marker("mark action item") == "action"
    assert match_marker("Flag.") == "flag"
    assert match_marker("mark that quote") == "quote"
    assert match_marker("this is just real speech") is None


def test_markers_from_entries_keeps_timestamp_and_type():
    entries = parse_transcript(
        "[0:14:32] [T3] mark flag\n"
        "[0:20:00] [T3] mark question please\n"
        "[0:21:00] [T3] uh hold on\n"          # not a marker -> ignored
    )
    ms = markers_from_entries(entries)
    assert [m["type"] for m in ms] == ["flag", "question"]
    assert ms[0]["t"] == 14 * 60 + 32
    assert ms[0]["text"] == "flag"


def test_embed_markers_sorts_marker_into_place():
    main = parse_transcript("[0:14:00] [You] before\n[0:15:00] [Caller] after\n")
    ms = [{"t": 14 * 60 + 32, "type": "flag", "text": "flag"}]
    out = embed_markers(main, ms)
    assert [e["label"] for e in out] == ["You", "MARKER", "Caller"]
    rendered = render_transcript(out)
    assert "[0:14:32] [MARKER] flag" in rendered


def test_embed_marker_lands_after_same_second_speech():
    # pressed just after hearing something at the same second -> marker sorts after it
    main = parse_transcript("[0:10:00] [Caller] the important bit\n")
    out = embed_markers(main, [{"t": 600, "type": "flag", "text": "flag"}])
    assert [e["label"] for e in out] == ["Caller", "MARKER"]


def test_no_markers_is_noop():
    main = parse_transcript("[0:00:05] [You] hi\n")
    assert embed_markers(main, []) == main
