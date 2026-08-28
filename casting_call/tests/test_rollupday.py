from casting_call.rollupday import day_summary, merge_rollups, report_name


def _call(name, yours=0, theirs=0, q=0, total=0):
    return {
        "name": name, "total": total, "aside_total": 0,
        "rollup": {
            "yours": [{"t": 10, "tstr": "0:00:10", "type": "action-me"}] * yours,
            "theirs": [{"t": 20, "tstr": "0:00:20", "type": "action-them"}] * theirs,
            "questions": [{"t": 30, "tstr": "0:00:30", "type": "question"}] * q,
            "unowned": [],
        },
    }


def test_report_name_sits_next_to_the_transcript():
    assert report_name("/x/y/call.txt") == "call-call.html"


def test_merge_tags_every_item_with_the_call_it_came_from():
    merged = merge_rollups([_call("a", yours=1), _call("b", yours=2)])
    assert len(merged["yours"]) == 3
    assert [i["call"] for i in merged["yours"]] == ["a", "b", "b"]


def test_merge_keeps_every_bucket():
    merged = merge_rollups([_call("a", yours=1, theirs=2, q=3)])
    assert (len(merged["yours"]), len(merged["theirs"]), len(merged["questions"])) == (1, 2, 3)


def test_merge_of_nothing_is_empty_buckets_not_a_crash():
    merged = merge_rollups([])
    assert merged == {"yours": [], "theirs": [], "questions": [], "unowned": []}


def test_day_summary_counts_calls_and_items():
    s = day_summary("26-08-25", [_call("a", yours=1, total=4), _call("b", theirs=2, total=3)])
    assert s["day"] == "26-08-25"
    assert s["calls"] == 2
    assert s["markers"] == 7
    assert s["counts"]["yours"] == 1
    assert s["counts"]["theirs"] == 2


def test_day_summary_carries_each_call_through_for_linking():
    s = day_summary("26-08-25", [_call("a"), _call("b")])
    assert [c["name"] for c in s["calls_list"]] == ["a", "b"]


def test_day_label_is_a_readable_date():
    assert day_summary("26-08-25", [])["label"] == "August 25, 2026"
    # anything that is not a yy-mm-dd directory falls back to its own name
    assert day_summary("misc", [])["label"] == "misc"


def test_remap_moves_legacy_action_into_yours():
    # Pre-2026-08-28 calls used one generic Action key that meant Sam's own to-do.
    # Without this the day page reads "Nothing landed on you" while the items sit
    # in a bucket the day template never prints.
    d = {"name": "a", "total": 1, "rollup": {
        "yours": [], "theirs": [], "questions": [],
        "unowned": [{"t": 5, "tstr": "0:00:05", "type": "action"}]}}
    merged = merge_rollups([d], remap={"action": "action-me"})
    assert len(merged["yours"]) == 1
    assert merged["unowned"] == []
    assert merged["yours"][0]["type"] == "action-me"


def test_remap_is_opt_in():
    d = {"name": "a", "total": 1, "rollup": {
        "yours": [], "theirs": [], "questions": [],
        "unowned": [{"t": 5, "tstr": "0:00:05", "type": "action"}]}}
    assert len(merge_rollups([d])["unowned"]) == 1


def test_day_summary_passes_remap_through_to_counts():
    d = {"name": "a", "total": 1, "rollup": {
        "yours": [], "theirs": [], "questions": [],
        "unowned": [{"t": 5, "tstr": "0:00:05", "type": "action"}]}}
    s = day_summary("26-08-04", [d], remap={"action": "action-me"})
    assert s["counts"]["yours"] == 1
    assert s["counts"]["unowned"] == 0


def test_per_call_tallies_follow_the_remap_too():
    # The day page's call cards read these, so they have to agree with the
    # roll-up above them rather than with the raw digest on disk.
    d = {"name": "a", "total": 1, "rollup": {
        "yours": [], "theirs": [], "questions": [],
        "unowned": [{"t": 5, "tstr": "0:00:05", "type": "action"}]}}
    s = day_summary("26-08-04", [d], remap={"action": "action-me"})
    assert s["calls_list"][0]["rollup"]["yours"] and not s["calls_list"][0]["rollup"]["unowned"]
