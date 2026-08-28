import base64

import casting_call.digest as digest
from casting_call.digest import (
    format_ts,
    split_markers,
    window_for,
    frame_data_uri,
    build_digest,
    resolve_artifacts,
)
from casting_call.transcript import parse_transcript


SAMPLE = (
    "[0:00:05] [You] kicking things off\n"
    "[0:00:10] [Caller] sounds good\n"
    "[0:14:20] [Caller] here is the roadmap slide\n"
    "[0:14:30] [MARKER] video\n"
    "[0:14:32] [You] wait go back one\n"
    "[0:14:40] [Caller] this quarter we ship X\n"
    "[0:20:00] [Caller] can you own the migration\n"
    "[0:20:05] [MARKER] action-me\n"
    "[0:20:08] [You] yeah I've got it\n"
    "[0:59:00] [Caller] far outside any marker window\n"
)


def test_format_ts_matches_transcript_format():
    assert format_ts(5) == "0:00:05"
    assert format_ts(14 * 60 + 32) == "0:14:32"
    assert format_ts(3661) == "1:01:01"


def test_split_markers_partitions_speech_and_markers():
    entries = parse_transcript(SAMPLE)
    speech, markers = split_markers(entries)
    assert all(e["label"] != "MARKER" for e in speech)
    assert [m["type"] for m in markers] == ["video", "action-me"]
    assert markers[0]["t"] == 14 * 60 + 30


def test_window_includes_within_and_excludes_outside():
    entries = parse_transcript(SAMPLE)
    speech, markers = split_markers(entries)
    win = window_for(markers[1], speech, 30)  # action at 20:05, +-30s
    texts = [e["text"] for e in win]
    assert "can you own the migration" in texts  # 20:00, within
    assert "yeah I've got it" in texts           # 20:08, within
    assert "this quarter we ship X" not in texts  # 14:40, outside
    assert "far outside any marker window" not in texts


def test_window_boundaries_are_inclusive():
    speech = [
        {"t": 100, "label": "You", "text": "low edge"},
        {"t": 160, "label": "You", "text": "high edge"},
        {"t": 161, "label": "You", "text": "just past"},
    ]
    marker = {"t": 130, "type": "topic"}
    texts = [e["text"] for e in window_for(marker, speech, 30)]
    assert texts == ["low edge", "high edge"]


def test_window_excludes_marker_lines():
    entries = parse_transcript(
        "[0:10:00] [You] real speech\n"
        "[0:10:02] [MARKER] topic\n"
        "[0:10:04] [MARKER] question\n"
    )
    speech, markers = split_markers(entries)
    win = window_for(markers[0], speech, 30)
    assert [e["text"] for e in win] == ["real speech"]


def test_window_entries_carry_tstr():
    speech = [{"t": 5, "label": "You", "text": "hi"}]
    win = window_for({"t": 5, "type": "topic"}, speech, 30)
    assert win[0]["tstr"] == "0:00:05"


def test_build_digest_groups_in_canonical_order():
    entries = parse_transcript(
        "[0:01:00] [MARKER] question\n"
        "[0:02:00] [MARKER] action-them\n"
        "[0:03:00] [MARKER] topic\n"
    )
    d = digest.build_digest_from_entries(entries, window_seconds=30, video_path=None)
    # canonical MARKER_TYPES order: action-them, action-me, topic, important,
    # question, quote, video, action
    assert [g["type"] for g in d["groups"]] == ["action-them", "topic", "question"]


def test_build_digest_counts_and_total():
    entries = parse_transcript(
        "[0:01:00] [MARKER] action-me\n"
        "[0:02:00] [MARKER] action-me\n"
        "[0:03:00] [MARKER] topic\n"
    )
    d = digest.build_digest_from_entries(entries, window_seconds=30, video_path=None)
    assert d["total"] == 3
    assert d["counts"] == {"action-me": 2, "topic": 1}


def test_build_digest_keeps_action_owners_apart():
    entries = parse_transcript(
        "[0:01:00] [MARKER] action-me\n"
        "[0:02:00] [MARKER] action-them\n"
        "[0:03:00] [MARKER] action\n"
    )
    d = digest.build_digest_from_entries(entries, window_seconds=30, video_path=None)
    assert d["counts"] == {"action-me": 1, "action-them": 1, "action": 1}
    # the bare fallback sorts last, after both owned buckets
    assert [g["type"] for g in d["groups"]] == ["action-them", "action-me", "action"]


def test_build_digest_no_markers_is_empty():
    entries = parse_transcript("[0:00:05] [You] just talking\n")
    d = digest.build_digest_from_entries(entries, window_seconds=30, video_path=None)
    assert d["total"] == 0
    assert d["groups"] == []


def test_build_digest_video_marker_gets_screenshot(monkeypatch):
    monkeypatch.setattr(digest, "frame_data_uri",
                        lambda video_path, t: "data:image/jpeg;base64,ZZZ")
    entries = parse_transcript(
        "[0:14:30] [MARKER] video\n"
        "[0:20:05] [MARKER] action-me\n"
    )
    d = digest.build_digest_from_entries(entries, window_seconds=30, video_path="/x/call.mp4")
    video_group = next(g for g in d["groups"] if g["type"] == "video")
    action_group = next(g for g in d["groups"] if g["type"] == "action-me")
    assert video_group["markers"][0]["screenshot"] == "data:image/jpeg;base64,ZZZ"
    assert action_group["markers"][0]["screenshot"] is None  # non-video never grabs a frame


def test_build_digest_video_marker_without_video_file():
    entries = parse_transcript("[0:14:30] [MARKER] video\n")
    d = digest.build_digest_from_entries(entries, window_seconds=30, video_path=None)
    assert d["groups"][0]["markers"][0]["screenshot"] is None


def test_frame_data_uri_wraps_ffmpeg_stdout(monkeypatch):
    class FakeProc:
        returncode = 0
        stdout = b"\xff\xd8jpegbytes"

    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(digest.subprocess, "run", fake_run)
    uri = frame_data_uri("/x/call.mp4", 872)
    assert uri == "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8jpegbytes").decode()
    # seeks to the marker time and asks ffmpeg for exactly one frame
    assert "-ss" in calls["cmd"] and "872" in calls["cmd"]
    assert "-frames:v" in calls["cmd"] and "1" in calls["cmd"]


def test_frame_data_uri_returns_none_on_ffmpeg_failure(monkeypatch):
    class FakeProc:
        returncode = 1
        stdout = b""

    monkeypatch.setattr(digest.subprocess, "run",
                        lambda cmd, **kwargs: FakeProc())
    assert frame_data_uri("/x/call.mp4", 5) is None


def test_frame_data_uri_returns_none_when_ffmpeg_missing(monkeypatch):
    def boom(cmd, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(digest.subprocess, "run", boom)
    assert frame_data_uri("/x/call.mp4", 5) is None


def test_resolve_artifacts_finds_siblings(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    transcript = tdir / "24-08-04-standup.txt"
    transcript.write_text("[0:00:05] [You] hi\n")
    # audio/video sit one level up, in the call folder
    (tmp_path / "24-08-04-standup.mp3").write_bytes(b"a")
    (tmp_path / "24-08-04-standup.mp4").write_bytes(b"v")
    audio, video = resolve_artifacts(transcript)
    assert audio.endswith("24-08-04-standup.mp3")
    assert video.endswith("24-08-04-standup.mp4")


def test_resolve_artifacts_prefers_mp4_over_mkv(tmp_path):
    transcript = tmp_path / "call.txt"
    transcript.write_text("[0:00:05] [You] hi\n")
    (tmp_path / "call.mkv").write_bytes(b"v")
    (tmp_path / "call.mp4").write_bytes(b"v")
    _, video = resolve_artifacts(transcript)
    assert video.endswith("call.mp4")


def test_resolve_artifacts_missing_returns_none(tmp_path):
    transcript = tmp_path / "call.txt"
    transcript.write_text("[0:00:05] [You] hi\n")
    audio, video = resolve_artifacts(transcript)
    assert audio is None and video is None
