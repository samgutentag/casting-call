import json
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from casting_call import fill
from casting_call.gaps import subtract_windows
from casting_call.marker_track import merge_cue_marks, parse_times

FIX = Path(__file__).parent / "fixtures" / "p4next"
MKA = FIX / "2026-11-14 22-08-45.mka"
LN = "loudnorm=I=-16:TP=-1.5:LRA=11"


def _wav(path, samples, rate=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(int(s).to_bytes(2, "little", signed=True) for s in samples))


def _samples(path):
    with wave.open(str(path)) as w:
        raw = w.readframes(w.getnframes())
    return [int.from_bytes(raw[i:i + 2], "little", signed=True) for i in range(0, len(raw), 2)]


def _extract(tmp_path):
    """The fixture's tracks the way ripa extracts them: 16 kHz mono, voices loudnormed."""
    out = {k: tmp_path / f"{k}.wav" for k in ("you", "caller", "markers")}
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(MKA),
                    "-map", "0:a:1", "-af", LN, "-ar", "16000", "-ac", "1", str(out["you"]),
                    "-map", "0:a:2", "-af", LN, "-ar", "16000", "-ac", "1", str(out["caller"]),
                    "-map", "0:a:3", "-ar", "16000", "-ac", "1", str(out["markers"])],
                   check=True)
    return out


def _zero(path, a, b):
    s = _samples(path)
    for i in range(int(a * 16000), min(len(s), int(b * 16000))):
        s[i] = 0
    _wav(path, s)


# ── pieces ───────────────────────────────────────────────────────────────────

def test_parse_silences_closes_an_open_span_at_eof():
    text = "silence_start: 0\nsilence_end: 2.5 | x\nsilence_start: 9.0\n"
    assert fill.parse_silences(text, 12.0) == [(0.0, 2.5), (9.0, 12.0)]


def test_dropout_candidates_take_digital_silence_and_long_quiet():
    got = fill.dropout_candidates([(10, 12)], [(100, 130), (200, 290)])
    assert got == [(10, 12), (200, 290)]


def test_split_by_card_needs_card_sound():
    covered, uncovered = fill.split_by_card([(0, 30), (100, 130)], [(100, 130)])
    assert covered == [(0, 30)] and uncovered == [(100, 130)]


def test_covered_spans_trim_card_silence_at_the_ends():
    covered, _u = fill.split_by_card([(74.0, 140.0)], [(70.0, 121.7)])
    assert covered == [(121.7, 140.0)]


def test_aligned_cmd_skips_or_delays_never_stretches(tmp_path):
    pos = fill.aligned_cmd(["-i", "c.wav"], 1.137, 60.0, tmp_path / "o.wav")
    assert pos[pos.index("-ss") + 1] == "1.1370" and "-t" in pos
    assert "atempo" not in " ".join(pos)
    neg = fill.aligned_cmd(["-i", "c.wav"], -2.5, 60.0, tmp_path / "o.wav", loudnorm=False)
    assert "-ss" not in neg
    assert neg[neg.index("-af") + 1] == "adelay=2500:all=1,apad"


def test_splice_replaces_only_the_spans(tmp_path):
    _wav(tmp_path / "obs.wav", [1000] * 48000)
    _wav(tmp_path / "card.wav", [-7] * 48000)
    fill.splice(tmp_path / "obs.wav", tmp_path / "card.wav", [(1.0, 2.0)], block=5000)
    s = _samples(tmp_path / "obs.wav")
    assert len(s) == 48000
    assert s[15999] == 1000 and s[16000] == -7 and s[31999] == -7 and s[32000] == 1000


def test_cue_marks_merge_with_pad_presses():
    pads = [{"t": 83, "at": 83.97, "type": "marker", "text": "marker"}]
    got = merge_cue_marks(pads, [84.5, 135.906])
    assert [m["t"] for m in got] == [83, 135]
    assert got[1]["at"] == 135.906 and got[1]["type"] == "marker"
    assert parse_times("1.5,,x,2") == [1.5, 2.0]


def test_caption_windows_skip_what_the_card_filled():
    assert subtract_windows([(0, 100)], [(20, 50)]) == [(0, 20), (50, 100)]
    assert subtract_windows([(0, 100)], []) == [(0, 100)]
    assert subtract_windows([(10, 20)], [(0, 30)]) == []


# ── the whole pass on the fixture ────────────────────────────────────────────

def test_no_card_take_changes_nothing(tmp_path):
    wavs = _extract(tmp_path)
    before = wavs["you"].read_bytes()
    assert fill.run(tmp_path, str(MKA), wavs) is None
    assert wavs["you"].read_bytes() == before
    assert not (tmp_path / "align.json").exists()


def test_mic_dropout_is_filled_from_the_card(tmp_path):
    name_dir = tmp_path / "rec"
    shutil.copytree(FIX / "card", tmp_path / "rec.card")
    wavs = _extract(tmp_path)
    _zero(wavs["you"], 0.0, 3.5)   # OBS mic dead over the fixture's first words
    summary = fill.run(name_dir, str(MKA), wavs, out=lambda s: None)

    saved = json.loads((name_dir / "align.json").read_text())
    assert saved["method"] == "markers" and saved["usable"]
    assert saved["offset"] == pytest.approx(1.137, abs=0.002)

    you = summary["tracks"]["you"]
    assert you["dropouts"][0][0] == pytest.approx(0.0, abs=0.05)
    assert you["filled"] and not you["uncovered"]
    assert max(abs(x) for x in _samples(wavs["you"])[:int(3.5 * 16000)]) > 100
    # The far side is digital silence on both OBS and the card here: real quiet.
    caller = summary["tracks"]["caller"]
    assert caller["dropouts"] and not caller["filled"] and caller["uncovered"]
    assert any("dropout filled from card MIC1 at" in line for line in summary["health"])
    assert summary["header"][0].startswith("# audio You: OBS, card MIC1 at")
    assert summary["cues"] == []      # the PLAY mark is past the end of the cut


def test_dead_marker_track_is_rebuilt_from_the_pad_file(tmp_path):
    name_dir = tmp_path / "rec"
    shutil.copytree(FIX / "card", tmp_path / "rec.card")
    wavs = _extract(tmp_path)
    _zero(wavs["markers"], 0.0, 99.0)
    summary = fill.run(name_dir, str(MKA), wavs, out=lambda s: None)
    assert summary["align"]["method"] == "speech"
    assert summary["markers"] == "card SOUND_PAD"
    assert max(abs(x) for x in _samples(wavs["markers"])) > 1000
