import casting_call.marker_track as mt
from casting_call.marker_track import parse_silence_log, bursts_to_markers


LOG = """
[Parsed_silencedetect_0 @ 0x1] silence_start: 0
[Parsed_silencedetect_0 @ 0x1] silence_end: 4.851458 | silence_duration: 4.851458
[Parsed_silencedetect_0 @ 0x1] silence_start: 5.571604
[Parsed_silencedetect_0 @ 0x1] silence_end: 8.970375 | silence_duration: 3.398771
[Parsed_silencedetect_0 @ 0x1] silence_start: 9.909625
"""


def test_parses_bursts_between_silences():
    bursts = parse_silence_log(LOG, duration=30.0)
    assert bursts == [(4.851458, 5.571604), (8.970375, 9.909625)]


def test_leading_sound_before_any_silence_is_a_burst():
    log = """
    silence_start: 2.0
    silence_end: 5.0 | silence_duration: 3.0
    silence_start: 6.0
    """
    assert parse_silence_log(log, duration=10.0)[0] == (0.0, 2.0)


def test_trailing_sound_runs_to_the_end_of_the_track():
    log = """
    silence_start: 0
    silence_end: 4.0 | silence_duration: 4.0
    """
    # no closing silence_start, so the burst runs to EOF
    assert parse_silence_log(log, duration=9.0) == [(4.0, 9.0)]


def test_a_fully_silent_track_has_no_bursts():
    log = "silence_start: 0\n"
    assert parse_silence_log(log, duration=60.0) == []


def test_empty_log_means_the_whole_track_is_one_burst():
    assert parse_silence_log("", duration=12.0) == [(0.0, 12.0)]


def test_bursts_become_markers_at_their_own_timestamps():
    # This is the bug the module exists for: whisper on the whole track collapsed
    # every press into one line at one timestamp. Each burst is transcribed alone
    # and keeps the second it actually happened.
    bursts = [(4.8, 5.5), (326.1, 327.0), (1857.0, 1858.0)]
    texts = ["Mark Topic.", " Mark out for them.", "Mark Question."]
    got = bursts_to_markers(bursts, texts)
    assert [m["type"] for m in got] == ["topic", "action-them", "question"]
    assert [m["t"] for m in got] == [4, 326, 1857]


def test_unrecognized_burst_is_dropped_not_guessed():
    got = bursts_to_markers([(1.0, 2.0), (5.0, 6.0)], ["a cough", "Mark Quote."])
    assert [m["type"] for m in got] == ["quote"]
    assert got[0]["t"] == 5


def test_marker_text_is_the_type_like_the_old_path():
    got = bursts_to_markers([(10.0, 11.0)], ["Mark Video."])
    assert got[0]["text"] == "video"


def test_detect_bursts_shells_out_and_parses(monkeypatch):
    calls = {}

    class R:
        returncode = 0
        stderr = LOG.encode()
        stdout = b""

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        return R()

    monkeypatch.setattr(mt.subprocess, "run", fake_run)
    monkeypatch.setattr(mt, "track_duration", lambda p: 30.0)
    out = mt.detect_bursts("/x/markers.mp3")
    assert out == [(4.851458, 5.571604), (8.970375, 9.909625)]
    assert "silencedetect" in " ".join(calls["cmd"])


def test_detect_bursts_returns_empty_when_ffmpeg_is_missing(monkeypatch):
    def boom(cmd, **kw):
        raise FileNotFoundError
    monkeypatch.setattr(mt.subprocess, "run", boom)
    assert mt.detect_bursts("/x/markers.mp3") == []


def test_transcribe_burst_gives_whisper_a_real_file_not_stdin(monkeypatch):
    # Regression: whisper-cli reads wav bytes from `-f -` but then reads the same
    # `-` as `--output-file -` and prints nothing, so piping silently produced an
    # empty transcription for every burst.
    seen = []

    class R:
        returncode = 0
        stdout = b"RIFFfake"
        stderr = b""

    class W:
        returncode = 0
        stdout = b"  Mark Topic.\n"
        stderr = b""

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return R() if cmd[0] == "ffmpeg" else W()

    monkeypatch.setattr(mt.subprocess, "run", fake_run)
    out = mt.transcribe_burst("/x/m.mp3", 10.0, 11.0, "whisper-cli", "/m.bin")
    assert out == "Mark Topic."
    whisper_cmd = seen[-1]
    path_arg = whisper_cmd[whisper_cmd.index("-f") + 1]
    assert path_arg != "-"
    assert path_arg.endswith(".wav")


def test_transcribe_burst_pads_the_slice_on_both_sides(monkeypatch):
    seen = []

    class R:
        returncode = 0
        stdout = b"RIFFfake"
        stderr = b""

    monkeypatch.setattr(mt.subprocess, "run",
                        lambda cmd, **kw: (seen.append(cmd), R())[1])
    mt.transcribe_burst("/x/m.mp3", 10.0, 11.0, "whisper-cli", "/m.bin", pad=0.4)
    cut = seen[0]
    assert cut[cut.index("-ss") + 1] == "9.6"
    assert cut[cut.index("-to") + 1] == "11.4"


def test_transcribe_burst_never_seeks_before_zero(monkeypatch):
    seen = []

    class R:
        returncode = 0
        stdout = b"RIFFfake"
        stderr = b""

    monkeypatch.setattr(mt.subprocess, "run",
                        lambda cmd, **kw: (seen.append(cmd), R())[1])
    mt.transcribe_burst("/x/m.mp3", 0.1, 1.0, "whisper-cli", "/m.bin", pad=0.4)
    assert seen[0][seen[0].index("-ss") + 1] == "0.0"
