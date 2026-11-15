import datetime as dt
import math
import subprocess
from pathlib import Path

import pytest

from casting_call import align

FIX = Path(__file__).parent / "fixtures" / "p4next"
CARD = FIX / "card"
MKA = FIX / "2026-11-14 22-08-45.mka"


def _track(tmp_path, index, name):
    out = tmp_path / name
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(MKA), "-map", f"0:a:{index}",
                    "-ar", "16000", "-ac", "1", str(out)], check=True)
    return out


# ── markers ──────────────────────────────────────────────────────────────────

def test_onsets_match_at_a_synthetic_offset_despite_strays():
    obs = [10.0, 55.5, 300.25]
    card = [t + 3.217 for t in obs] + [400.0]      # one stray burst on the card
    offset, matched, conf = align.offset_from_onsets(obs, card)
    assert offset == pytest.approx(3.217, abs=1e-6)
    assert matched == 3 and conf == 1.0


def test_negative_offset_when_the_card_started_late():
    obs = [20.0, 80.0]
    offset, _m, _c = align.offset_from_onsets(obs, [t - 4.5 for t in obs])
    assert offset == pytest.approx(-4.5)


def test_no_presses_gives_nothing():
    assert align.offset_from_onsets([], [1.0]) is None
    assert align.offset_from_onsets([1.0], []) is None


def test_one_lucky_match_among_many_presses_is_low_confidence():
    obs = [10.0, 20.0, 30.0, 40.0]
    card = [15.0, 33.0, 47.0, 61.0]
    _o, matched, conf = align.offset_from_onsets(obs, card)
    assert matched == 1 and conf < align.CONFIDENCE_FLOOR


def test_markers_method_on_the_fixture(tmp_path):
    res = align.align_markers(_track(tmp_path, 3, "mk.wav"), CARD / "261114_220657_SOUND_PAD.wav")
    assert res["method"] == "markers"
    assert res["offset"] == pytest.approx(1.137, abs=0.002)
    assert res["confidence"] == 1.0


# ── speech ───────────────────────────────────────────────────────────────────

def _bursty(n, seed):
    out, x = [], seed
    for i in range(n):
        x = (x * 1103515245 + 12345) % 2 ** 31
        out.append(100.0 if (x >> 16) % 7 == 0 else 1.0 + math.sin(i / 3.0))
    return out


def test_xcorr_finds_a_known_lag():
    x = _bursty(3000, 7)
    y = [0.0] * 250 + x
    lag, r = align.xcorr_lag(x, y)
    assert lag == 250 and r > 0.99


def test_speech_offset_from_envelopes():
    obs = _bursty(20000, 3)              # 20 s at 1 kHz
    card = [0.0] * 1137 + obs            # card started 1.137 s earlier
    offset, r = align.offset_from_envelopes(obs, card)
    assert offset == pytest.approx(1.137, abs=0.002) and r > 0.9


def test_speech_skips_a_dead_obs_mic_and_uses_usb():
    envs = {"you": [0.0] * 5000, "mic": _bursty(5000, 1),
            "caller": _bursty(5000, 9), "usb": [0.0] * 500 + _bursty(5000, 9)}
    res = align.align_speech([("you", "mic", "Track 2 / MIC1"),
                              ("caller", "usb", "Track 3 / USB")], env=envs.get)
    assert res["detail"].startswith("Track 3 / USB")
    assert res["offset"] == pytest.approx(0.5, abs=0.002)


def test_speech_on_the_fixture_mic(tmp_path):
    res = align.align_speech([(_track(tmp_path, 1, "you.wav"),
                               CARD / "261114_220657_MIC1.wav", "mic")])
    # OBS Track 2 runs about 62 ms behind the pad path on this rig.
    assert res["offset"] == pytest.approx(1.20, abs=0.03)
    assert res["confidence"] >= align.CONFIDENCE_FLOOR


# ── clock and choice ─────────────────────────────────────────────────────────

def test_clock_is_approximate_and_never_usable_alone():
    res = align.align_take(card_start=dt.datetime(2026, 11, 14, 22, 8, 21),
                           video_start=dt.datetime(2026, 11, 14, 22, 8, 45))
    assert res["method"] == "clock" and res["offset"] == 24.0
    assert res["approximate"] and not res["usable"]


def test_markers_win_when_presses_match():
    bursts = {"obs": [(5.0, 5.5), (60.0, 60.5)], "card": [(6.2, 6.7), (61.2, 61.7)]}
    res = align.align_take(obs_markers="obs", card_pads="card",
                           speech_pairs=[("a", "b", "x")],
                           detect=bursts.get, env=lambda p: [])
    assert res["method"] == "markers" and res["usable"]
    assert res["offset"] == pytest.approx(1.2)


def test_no_presses_falls_back_to_speech_then_clock():
    envs = {"a": _bursty(4000, 5), "b": [0.0] * 300 + _bursty(4000, 5)}
    res = align.align_take(obs_markers="obs", card_pads="card",
                           speech_pairs=[("a", "b", "mic")],
                           detect=lambda p: [], env=envs.get)
    assert res["method"] == "speech" and res["offset"] == pytest.approx(0.3, abs=0.002)
    res = align.align_take(obs_markers="obs", card_pads="card",
                           speech_pairs=[("a", "b", "mic")],
                           detect=lambda p: [], env=lambda p: [0.0] * 100)
    assert res is None


def test_write_and_load_align(tmp_path):
    align.write_align(tmp_path, {"offset": 1.137, "method": "markers",
                                 "confidence": 1.0, "usable": True})
    assert align.load_align(tmp_path)["offset"] == 1.137
    assert align.main(["offset", str(tmp_path)]) == 0
    align.write_align(tmp_path, {"offset": 24.0, "usable": False})
    assert align.main(["offset", str(tmp_path)]) == 1
    assert align.load_align(tmp_path / "nope") is None
