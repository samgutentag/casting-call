import io
import json
import urllib.error
import urllib.parse

import pytest

from casting_call import deepgram, names


def _w(word, start, end, speaker=0):
    return {"word": word.lower().strip(".?!"), "punctuated_word": word,
            "start": start, "end": end, "speaker": speaker}


def _response(words):
    return {"results": {"channels": [{"alternatives": [{"words": words}]}]}}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_every_url_opts_out_of_model_improvement():
    for diarize in (False, True):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(deepgram.build_url(diarize)).query)
        assert q["mip_opt_out"] == ["true"]
        assert q["model"] == ["nova-3"]


def test_opt_out_survives_someone_editing_the_base_params(monkeypatch):
    monkeypatch.setitem(deepgram.BASE_PARAMS, "mip_opt_out", "false")
    assert "mip_opt_out=true" in deepgram.build_url()


def test_diarize_only_when_asked():
    assert "diarize" not in deepgram.build_url(False)
    assert "diarize=true" in deepgram.build_url(True)


def test_lines_split_on_speaker_change_and_pause():
    words = [_w("Hi", 0, .4), _w("there.", .5, .9), _w("Yo.", 1.0, 1.3, speaker=1),
             _w("Later", 4.0, 4.4, speaker=1)]
    lines = deepgram.to_lines(words)
    assert [(l["speaker"], l["text"]) for l in lines] == [(0, "Hi there."), (1, "Yo."),
                                                          (1, "Later")]


def test_long_turn_splits_at_a_sentence_end_after_the_soft_limit():
    words = []
    t = 0.0
    for n in range(60):
        words.append(_w("word." if n % 10 == 9 else "word", t, t + .4))
        t += .5
    lines = deepgram.to_lines(words)
    assert len(lines) > 1
    assert all(l["text"].endswith(".") for l in lines[:-1])
    assert all(l["end"] - l["start"] <= deepgram.HARD_MAX for l in lines)


def test_srt_matches_the_whisper_shape():
    srt = deepgram.to_srt([{"start": 61.25, "end": 63.0, "speaker": 0, "text": "Hello."}])
    assert srt == "1\n00:01:01,250 --> 00:01:03,000\nHello.\n\n"


def test_http_error_falls_back_without_retrying_a_bad_key(tmp_path):
    audio = tmp_path / "a.ogg"
    audio.write_bytes(b"x")
    calls = []

    def urlopen(req, timeout):
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)
    resp, reason = deepgram.post(audio, "k", urlopen=urlopen, sleep=lambda s: None)
    assert resp is None and reason == "HTTP 401" and len(calls) == 1


def test_network_errors_retry_then_give_up(tmp_path):
    audio = tmp_path / "a.ogg"
    audio.write_bytes(b"x")
    calls = []

    def urlopen(req, timeout):
        calls.append(req)
        raise urllib.error.URLError("timed out")
    resp, reason = deepgram.post(audio, "k", urlopen=urlopen, sleep=lambda s: None)
    assert resp is None and reason.startswith("network")
    assert len(calls) == deepgram.RETRIES + 1


def test_transcribe_writes_srt_raw_and_speakers(tmp_path, monkeypatch):
    monkeypatch.setattr(deepgram, "encode_opus", lambda wav, out: out.write_bytes(b"ogg"))
    body = json.dumps(_response([_w("Hi.", 0, .5), _w("Yo.", 2.5, 3, 1)])).encode()
    seen = {}

    def urlopen(req, timeout):
        seen["url"], seen["auth"] = req.full_url, req.headers["Authorization"]
        return _Resp(body)
    srt, raw, spk = tmp_path / "c.srt", tmp_path / "c.json", tmp_path / "s.json"
    n, reason = deepgram.transcribe(tmp_path / "c.wav", srt, raw, True, spk,
                                    key="secret-key", urlopen=urlopen)
    assert (n, reason) == (2, None)
    assert "mip_opt_out=true" in seen["url"] and "diarize=true" in seen["url"]
    assert json.loads(spk.read_text()) == [
        {"t": 0, "start": 0.0, "end": 0.5, "speaker": 0},
        {"t": 2, "start": 2.5, "end": 3.0, "speaker": 1}]
    assert "secret-key" not in srt.read_text() + raw.read_text() + spk.read_text()


def test_missing_key_is_a_reason_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(deepgram, "load_key", lambda: None)
    assert deepgram.transcribe(tmp_path / "x.wav", tmp_path / "x.srt") == (None, "no DEEPGRAM_API_KEY")


def test_cli_failure_prints_reason_not_key(monkeypatch, capsys):
    monkeypatch.setattr(deepgram, "transcribe", lambda *a, **k: (None, "HTTP 401"))
    monkeypatch.setenv("DEEPGRAM_API_KEY", "secret-key")
    assert deepgram.main(["transcribe", "x.wav", "--srt", "x.srt"]) == 3
    out = capsys.readouterr()
    assert "HTTP 401" in out.out and "secret-key" not in out.out + out.err


# --- naming by Deepgram speaker ---------------------------------------------

def _words(spans):
    """Caption words: [(t, name)] one every 0.5 s across each (start, end, name)."""
    out = []
    for start, end, name in spans:
        t = start
        while t < end:
            out.append((t, name))
            t += .5
    return out


def test_two_speakers_under_one_name_become_second_voice():
    speakers = {0: (0, 0.0, 30.0), 40: (1, 40.0, 50.0), 60: (2, 60.0, 80.0)}
    words = _words([(0, 30, "Ed"), (40, 50, "Ed"), (60, 80, "Naomi")])
    labels, summary = names.name_speakers(speakers, words, ["Ed", "Naomi"])
    assert labels == {0: "Ed", 1: "Ed (second voice)", 2: "Naomi"}
    assert summary["1"]["label"] == "Ed (second voice)"


def test_speaker_without_a_clear_majority_stays_unnamed():
    speakers = {0: (0, 0.0, 20.0)}
    words = _words([(0, 10, "Ed"), (10, 20, "Naomi")])
    labels, _ = names.name_speakers(speakers, words, ["Ed", "Naomi"])
    assert labels == {0: None}


def test_clear_leader_below_eighty_percent_is_named():
    # 7 s of Dana, 2 s of Ed: 70% with a 3.5x lead, as real crosstalk looks.
    speakers = {0: (0, 0.0, 9.0)}
    words = _words([(0, 7, "Dana"), (7, 9, "Ed")])
    labels, _ = names.name_speakers(speakers, words, ["Dana", "Ed"])
    assert labels == {0: "Dana"}


def test_lines_follow_their_speaker_and_spliced_lines_vote_alone():
    entries = [{"t": 0, "label": "Caller", "text": "a"},
               {"t": 40, "label": "Caller", "text": "b"},
               {"t": 70, "label": "Caller", "text": "spliced from captions"}]
    speakers = {0: (0, 0.0, 30.0), 40: (1, 40.0, 50.0)}
    words = _words([(0, 30, "Ed"), (40, 50, "Ed"), (68, 80, "Naomi")])
    out, records, _ = names.name_lines_by_speaker(entries, words, ["Ed", "Naomi"], speakers)
    assert [e["label"] for e in out] == ["Ed", "Ed (second voice)", "Naomi"]
    assert [r["speaker"] for r in records] == [0, 1, None]


def test_load_speakers_tolerates_a_missing_file(tmp_path):
    assert names.load_speakers(tmp_path / "nope.json") == {}
