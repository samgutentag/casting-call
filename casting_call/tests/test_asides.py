from casting_call.asides import match_aside, asides_from_entries
from casting_call.transcript import parse_transcript


def test_note_phrases_match():
    assert match_aside("notes notes notes the nav labels are stale") == "note"
    assert match_aside("note that the SDK is pinned") == "note"
    assert match_aside("note to self, check the quota") == "note"
    assert match_aside("make a note about the rate limit") == "note"
    assert match_aside("I'll note that down") == "note"
    assert match_aside("taking a note of this") == "note"
    assert match_aside("I'm noting this") == "note"


def test_action_phrases_match():
    assert match_aside("action item, send the SOW") == "action"
    assert match_aside("this is for me to do") == "action"
    assert match_aside("this is an action item for Dana") == "action"


def test_bare_commitments_are_not_asides():
    # Sam says these constantly in a normal sync. They belong to the model's
    # inferred pass, not the authoritative flagged channel.
    assert match_aside("I'll send that over tomorrow") is None
    assert match_aside("I need to check the dashboard") is None
    assert match_aside("I have to rerun the migration") is None
    assert match_aside("just regular conversation here") is None


def test_listed_note_phrase_beats_the_bare_commitment_rule():
    # "I'll note that down" starts like a bare commitment but is a listed phrase.
    assert match_aside("I'll note that down, the nav labels are stale") == "note"


def test_asides_only_read_the_you_channel():
    entries = parse_transcript(
        "[0:01:00] [You] action item, send the SOW\n"
        "[0:02:00] [Caller] action item, that one is on us\n"
        "[0:03:00] [MARKER] action-me\n"
    )
    got = asides_from_entries(entries)
    assert [a["type"] for a in got] == ["action"]
    assert got[0]["t"] == 60


def test_aside_carries_timestamp_and_full_line():
    entries = parse_transcript("[0:14:32] [You] note that the nav labels are stale\n")
    got = asides_from_entries(entries)
    assert got[0]["t"] == 14 * 60 + 32
    assert got[0]["text"] == "note that the nav labels are stale"
    assert got[0]["phrase"] == "note that"


def test_self_label_is_configurable():
    entries = parse_transcript("[0:01:00] [Sam] action item, ship it\n")
    assert asides_from_entries(entries, self_label="Sam")[0]["type"] == "action"
    assert asides_from_entries(entries) == []
