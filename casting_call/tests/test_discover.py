from casting_call.discover import find_transcripts, group_by_day


def _mk(tmp_path, *rels):
    for r in rels:
        p = tmp_path / r
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("[0:00:01] [You] hi\n")
    return tmp_path


def test_finds_both_layouts(tmp_path):
    root = _mk(tmp_path,
               "26-08-25/2026-08-25-corey-sync/2026-08-25-corey-sync.txt",
               "26-07-20/transcripts/26-07-20-corey-sync.txt")
    got = [p.name for p in find_transcripts(root)]
    assert sorted(got) == ["2026-08-25-corey-sync.txt", "26-07-20-corey-sync.txt"]


def test_excludes_parts_dir(tmp_path):
    # parts/ holds the per-track reference transcripts, not calls. Three garbage
    # "calls" per recording if this is not filtered.
    root = _mk(tmp_path,
               "26-08-25/call/call.txt",
               "26-08-25/call/parts/you.txt",
               "26-08-25/call/parts/caller.txt",
               "26-08-25/call/parts/markers.txt")
    assert [p.name for p in find_transcripts(root)] == ["call.txt"]


def test_excludes_channel_split_variants(tmp_path):
    root = _mk(tmp_path, "d/c/c.txt", "d/c/c-left.txt", "d/c/c-right.txt")
    assert [p.name for p in find_transcripts(root)] == ["c.txt"]


def test_excludes_generated_report_siblings(tmp_path):
    root = _mk(tmp_path, "d/c/c.txt", "d/c/c-summary.md")
    assert [p.name for p in find_transcripts(root)] == ["c.txt"]


def test_dedupes_the_same_call_in_two_locations(tmp_path):
    # The per-recording folder is the current layout; transcripts/ is the old one.
    # Same stem in both means one call, and the newer layout wins.
    root = _mk(tmp_path,
               "26-07-24/2026-07-24-events-sync/2026-07-24-events-sync.txt",
               "26-07-24/transcripts/2026-07-24-events-sync.txt")
    got = find_transcripts(root)
    assert len(got) == 1
    assert got[0].parent.name == "2026-07-24-events-sync"


def test_prefers_a_clean_variant_over_the_raw_one(tmp_path):
    root = _mk(tmp_path,
               "d/transcripts/call.txt",
               "d/transcripts/call-clean.txt",
               "d/transcripts/call-stitched.txt")
    got = find_transcripts(root)
    assert len(got) == 1
    assert got[0].name == "call-clean.txt"


def test_audio_only_copy_loses_to_transcripts_copy(tmp_path):
    root = _mk(tmp_path,
               "d/audio_only/call-clean.txt",
               "d/transcripts/call-clean.txt")
    got = find_transcripts(root)
    assert len(got) == 1
    assert got[0].parent.name == "transcripts"


def test_a_single_transcript_path_resolves_to_itself(tmp_path):
    root = _mk(tmp_path, "d/c/c.txt")
    p = root / "d/c/c.txt"
    assert find_transcripts(p) == [p]


def test_groups_by_day_directory(tmp_path):
    root = _mk(tmp_path,
               "26-08-25/a/a.txt", "26-08-25/b/b.txt", "26-08-28/c/c.txt")
    days = group_by_day(find_transcripts(root), root)
    assert [d for d, _ in days] == ["26-08-25", "26-08-28"]
    assert len(days[0][1]) == 2


def test_day_grouping_of_a_single_day_target(tmp_path):
    # Target IS the day dir, so the day is that dir's own name.
    root = _mk(tmp_path, "26-08-25/a/a.txt", "26-08-25/b/b.txt")
    day_dir = root / "26-08-25"
    days = group_by_day(find_transcripts(day_dir), day_dir)
    assert [d for d, _ in days] == ["26-08-25"]
    assert len(days[0][1]) == 2
