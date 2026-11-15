import json

import casting_call.marker_track as mt
from casting_call import frames
from casting_call.digest import MARKER_WINDOW, build_digest_from_entries
from casting_call.marker_track import (choose_mode, markers_from_bursts,
                                       presses_from_bursts)


# ── mode choice ──────────────────────────────────────────────────────────────

def test_stream_deck_phrase_means_legacy():
    assert choose_mode(["", "Mark Topic."]) == "legacy"


def test_marker_word_or_nothing_means_single():
    assert choose_mode(["Marker.", None, ""]) == "single"
    assert choose_mode([]) == "single"


# ── pad presses ──────────────────────────────────────────────────────────────

def test_each_burst_is_one_marker_at_its_exact_start():
    got = presses_from_bursts([(12.34, 12.9), (300.5, 301.1)])
    assert [m["t"] for m in got] == [12, 300]
    assert [m["at"] for m in got] == [12.34, 300.5]
    assert {m["type"] for m in got} == {"marker"}
    assert {m["text"] for m in got} == {"marker"}


def test_double_tap_folds_into_one_press():
    # second press lands just after the 0.74s clip ends
    got = presses_from_bursts([(10.0, 10.6), (11.0, 11.6), (20.0, 20.6)])
    assert [m["t"] for m in got] == [10, 20]


def test_short_clicks_are_not_presses():
    got = presses_from_bursts([(5.0, 5.05), (9.0, 9.6)])
    assert [m["t"] for m in got] == [9]


def test_auto_single_stops_calling_whisper_after_the_probe():
    bursts = [(float(i * 10), i * 10 + 0.6) for i in range(8)]
    calls = []

    def fake(s, e):
        calls.append(s)
        return "Marker."

    markers, mode, texts = markers_from_bursts(bursts, fake)
    assert mode == "single"
    assert len(calls) == mt.PROBE_BURSTS
    assert len(markers) == 8
    assert texts[mt.PROBE_BURSTS:] == [None] * (8 - mt.PROBE_BURSTS)


def test_auto_legacy_transcribes_every_burst():
    bursts = [(1.0, 2.0), (50.0, 51.0), (90.0, 91.0), (120.0, 121.0)]
    said = ["Mark Topic.", "Mark Quote.", "a cough", "Mark Video."]
    markers, mode, _ = markers_from_bursts(bursts, lambda s, e: said[bursts.index((s, e))])
    assert mode == "legacy"
    assert [m["type"] for m in markers] == ["topic", "quote", "video"]


def test_forced_single_never_calls_whisper():
    def boom(s, e):
        raise AssertionError("whisper called")

    markers, mode, _ = markers_from_bursts([(3.0, 3.6)], boom, mode="single")
    assert mode == "single" and markers[0]["type"] == "marker"


# ── frame grabs ──────────────────────────────────────────────────────────────

def test_frame_cmd_is_native_resolution_and_seeks_before_input():
    cmd = frames.frame_cmd("/x/call.mkv", 754.25, "/x/call/markers/marker-001234.png")
    assert cmd[cmd.index("-ss") + 1] == "754.250"
    assert cmd.index("-ss") < cmd.index("-i")
    assert cmd[cmd.index("-frames:v") + 1] == "1"
    assert "-vf" not in cmd and "scale" not in " ".join(cmd)
    assert cmd[-1].endswith(".png")


def test_frame_names_are_hhmmss_and_unique_within_a_second():
    taken = set()
    assert frames.frame_name(3723.4, taken) == "marker-010203.png"
    assert frames.frame_name(3723.9, taken) == "marker-010203-2.png"


def test_grab_frames_reports_failures_as_none(tmp_path):
    class R:
        def __init__(self, rc):
            self.returncode = rc

    def fake_run(cmd, **kw):
        out = cmd[-1]
        if "000010" in out:
            open(out, "wb").write(b"png")
            return R(0)
        return R(1)

    got = frames.grab_frames("v.mkv", [{"t": 10, "at": 10.2}, {"t": 20}],
                             tmp_path / "markers", run=fake_run)
    assert got[0].endswith("marker-000010.png") and got[1] is None


def test_manifest_round_trip(tmp_path):
    p = tmp_path / "markers.json"
    frames.write_manifest(p, "v.mkv", [{"t": 65, "at": 65.4, "type": "marker"}],
                          ["/f/marker-000105.png"])
    data = json.loads(p.read_text())
    assert data["markers"][0]["tstr"] == "0:01:05"
    assert frames.load_manifest(p) == {65: "/f/marker-000105.png"}
    assert frames.load_manifest(tmp_path / "missing.json") == {}


# ── digest ───────────────────────────────────────────────────────────────────

def test_digest_gives_single_markers_a_wide_window_and_the_frame():
    entries = [
        {"t": 0, "label": "You", "text": "far before"},
        {"t": 100, "label": "Caller", "text": "context line"},
        {"t": 160, "label": "MARKER", "text": "marker"},
        {"t": 160 + MARKER_WINDOW, "label": "You", "text": "edge"},
    ]
    d = build_digest_from_entries(entries, window_seconds=30,
                                  frames={160: "/f/marker-000240.png"})
    m = d["groups"][0]["markers"][0]
    assert m["type"] == "marker"
    assert m["frame"] == "/f/marker-000240.png"
    assert [c["text"] for c in m["context"]] == ["context line", "edge"]
