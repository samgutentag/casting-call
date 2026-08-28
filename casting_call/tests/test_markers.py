from casting_call.transcript import parse_transcript, render_transcript
from casting_call.markers import match_marker, markers_from_entries, embed_markers


def test_match_marker_finds_keyword_anywhere():
    assert match_marker("mark important thing") == "important"
    assert match_marker("Topic.") == "topic"
    assert match_marker("mark that quote") == "quote"
    assert match_marker("this is just real speech") is None


def test_match_marker_splits_action_by_owner():
    # The whole point of the ordered table: "action for them" contains "action",
    # so the owner rules have to be consulted before the bare fallback.
    assert match_marker("mark action for me") == "action-me"
    assert match_marker("mark action for them") == "action-them"
    assert match_marker("Mark action for them.") == "action-them"


def test_match_marker_falls_back_when_owner_is_clipped():
    # Whisper eating the unstressed trailing word lands here rather than being
    # silently misfiled onto one side or the other.
    assert match_marker("mark action") == "action"
    assert match_marker("mark action item") == "action"


def test_match_marker_retired_keywords_no_longer_match():
    assert match_marker("mark flag") is None
    assert match_marker("mark follow up") is None
    assert match_marker("mark follow-up") is None


def test_match_marker_handles_remaining_keywords():
    assert match_marker("mark video") == "video"
    assert match_marker("mark question") == "question"
    assert match_marker("mark topic") == "topic"


def test_markers_from_entries_keeps_timestamp_and_type():
    entries = parse_transcript(
        "[0:14:32] [T3] mark topic\n"
        "[0:20:00] [T3] mark question please\n"
        "[0:21:00] [T3] uh hold on\n"          # not a marker -> ignored
    )
    ms = markers_from_entries(entries)
    assert [m["type"] for m in ms] == ["topic", "question"]
    assert ms[0]["t"] == 14 * 60 + 32
    assert ms[0]["text"] == "topic"


def test_embed_markers_sorts_marker_into_place():
    main = parse_transcript("[0:14:00] [You] before\n[0:15:00] [Caller] after\n")
    ms = [{"t": 14 * 60 + 32, "type": "topic", "text": "topic"}]
    out = embed_markers(main, ms)
    assert [e["label"] for e in out] == ["You", "MARKER", "Caller"]
    rendered = render_transcript(out)
    assert "[0:14:32] [MARKER] topic" in rendered


def test_embedded_action_owner_type_round_trips():
    # Hyphenated types have to survive render -> parse, since digest.py reads
    # the type straight back off the rendered line.
    main = parse_transcript("[0:14:00] [You] before\n")
    ms = [{"t": 14 * 60 + 10, "type": "action-them", "text": "action-them"}]
    rendered = render_transcript(embed_markers(main, ms))
    assert "[0:14:10] [MARKER] action-them" in rendered
    reparsed = parse_transcript(rendered)
    assert reparsed[1]["label"] == "MARKER"
    assert reparsed[1]["text"] == "action-them"


def test_embed_marker_lands_after_same_second_speech():
    # pressed just after hearing something at the same second -> marker sorts after it
    main = parse_transcript("[0:10:00] [Caller] the important bit\n")
    out = embed_markers(main, [{"t": 600, "type": "topic", "text": "topic"}])
    assert [e["label"] for e in out] == ["Caller", "MARKER"]


def test_no_markers_is_noop():
    main = parse_transcript("[0:00:05] [You] hi\n")
    assert embed_markers(main, []) == main
