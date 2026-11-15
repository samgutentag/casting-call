import datetime as dt
import shutil
import struct
import wave
from pathlib import Path

import pytest

from casting_call import card

FIX = Path(__file__).parent / "fixtures" / "p4next"
TAKE = "261114_220657"


def _write_wav(path, frames, rate=8000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * frames)


# ── names and roles ──────────────────────────────────────────────────────────

def test_classify_maps_suffixes_to_roles():
    assert card.classify(f"{TAKE}.wav") == (TAKE, "mix", 1)
    assert card.classify(f"{TAKE}_MIC1.wav") == (TAKE, "mic", 1)
    assert card.classify(f"{TAKE}_USB.wav") == (TAKE, "usb", 1)
    assert card.classify(f"{TAKE}_SOUND_PAD.wav") == (TAKE, "pads", 1)
    assert card.classify(f"{TAKE}_MIC3.wav") == (TAKE, "ignored", 1)


def test_classify_reads_split_parts_and_rejects_strangers():
    assert card.classify(f"{TAKE}_USB_02.wav") == (TAKE, "usb", 2)
    assert card.classify(f"{TAKE}_02.wav") == (TAKE, "mix", 2)
    assert card.classify("notes.wav") is None
    assert card.classify(f"{TAKE}_LINE.wav") is None


def test_find_card_take_on_the_fixture(tmp_path):
    shutil.copytree(FIX / "card", tmp_path / "card")
    (tmp_path / "card" / "readme.txt").write_text("x")
    take = card.find_card_take(tmp_path)
    assert take.take == TAKE
    assert set(take.roles) == {"mix", "mic", "usb", "pads"}
    assert [p.name for p in take.ignored] == [f"{TAKE}_MIC2.wav"]
    assert [p.name for p in take.unknown] == ["readme.txt"]


def test_no_card_folder_means_no_take(tmp_path):
    assert card.find_card_take(tmp_path) is None
    (tmp_path / "card").mkdir()
    assert card.find_card_take(tmp_path) is None


# ── bext and cue ─────────────────────────────────────────────────────────────

def test_fixture_bext_and_cue_are_read():
    info = card.read_wav_info(FIX / "card" / f"{TAKE}.wav")
    assert (info.rate, info.channels, info.bits) == (8000, 1, 16)
    assert info.frames == 51200
    assert info.origin == dt.datetime(2026, 11, 14, 22, 8, 21)
    assert info.time_reference == 637612800
    # The real PLAY mark (137.04 s card time) sits past the end of the cut.
    assert info.cues == [419541]
    assert info.cue_seconds[0] == pytest.approx(137.0426 - 84.6, abs=0.001)


def test_parse_cue_reads_sample_offsets_in_order():
    body = struct.pack("<I", 2)
    body += struct.pack("<II4sIII", 2, 900, b"data", 0, 0, 900)
    body += struct.pack("<II4sIII", 1, 100, b"data", 0, 0, 100)
    assert card.parse_cue(body) == [100, 900]
    assert card.parse_cue(b"") == []


def test_parse_bext_handles_short_or_blank_chunks():
    assert card.parse_bext(b"\0" * 10) == (None, None)
    origin, ref = card.parse_bext(b"\0" * 346)
    assert origin is None and ref == 0


def test_read_wav_info_rejects_non_wav(tmp_path):
    p = tmp_path / "x.wav"
    p.write_bytes(b"not a wav at all")
    with pytest.raises(ValueError):
        card.read_wav_info(p)


def test_take_start_prefers_bext_then_name(tmp_path):
    assert card.take_start(FIX / "card" / f"{TAKE}.wav") == dt.datetime(2026, 11, 14, 22, 8, 21)
    plain = tmp_path / f"{TAKE}.wav"
    _write_wav(plain, 10)
    assert card.take_start(plain) == dt.datetime(2026, 11, 14, 22, 6, 57)


def test_obs_start_from_file_name():
    assert card.obs_start("/x/2026-11-14 22-07-21.mkv") == dt.datetime(2026, 11, 14, 22, 7, 21)
    assert card.obs_start("/x/call.mkv") is None


# ── split parts ──────────────────────────────────────────────────────────────

def test_split_parts_join_in_order(tmp_path):
    d = tmp_path / "card"
    d.mkdir()
    _write_wav(d / f"{TAKE}_USB.wav", 8000)
    _write_wav(d / f"{TAKE}_USB_02.wav", 4000)
    take = card.find_card_take(tmp_path)
    assert [p.name for p in take.roles["usb"]] == [f"{TAKE}_USB.wav", f"{TAKE}_USB_02.wav"]
    joined = card.join_parts(take.roles["usb"], tmp_path / "usb.wav")
    with wave.open(str(joined)) as w:
        assert w.getnframes() == 12000
    assert card.join_parts(take.roles["usb"][:1], tmp_path / "x.wav") == take.roles["usb"][0]


def test_input_args_single_file_and_concat(tmp_path):
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    assert card.input_args([a], tmp_path / "l.txt") == ["-i", str(a)]
    args = card.input_args([a, b], tmp_path / "l.txt")
    assert args[:4] == ["-f", "concat", "-safe", "0"]
    assert (tmp_path / "l.txt").read_text().count("file '") == 2


# ── copying off the card ─────────────────────────────────────────────────────

def test_match_takes_allows_a_slow_card_clock():
    recs = [("2026-11-14 22-07-21", dt.datetime(2026, 11, 14, 22, 7, 21), 142.0),
            ("2026-11-14 23-00-00", dt.datetime(2026, 11, 14, 23, 0, 0), 600.0)]
    takes = {TAKE: dt.datetime(2026, 11, 14, 22, 6, 57),
             "261114_120000": dt.datetime(2026, 11, 14, 12, 0, 0)}
    matched, left = card.match_takes(recs, takes)
    assert matched == {TAKE: "2026-11-14 22-07-21"}
    assert left == ["261114_120000"]


def test_card_takes_reads_card_and_inbox_layouts(tmp_path):
    vol = tmp_path / "P4NEXT_SD"
    multi = vol / card.MULTITRACK_DIR / TAKE
    multi.mkdir(parents=True)
    shutil.copy(FIX / "card" / f"{TAKE}.wav", vol)
    shutil.copy(FIX / "card" / f"{TAKE}_USB.wav", multi)
    inbox = tmp_path / "inbox" / TAKE
    inbox.mkdir(parents=True)
    shutil.copy(FIX / "card" / f"{TAKE}.wav", inbox)
    shutil.copy(FIX / "card" / f"{TAKE}_USB.wav", inbox)
    for root in (vol, tmp_path / "inbox"):
        assert sorted(p.name for p in card.card_takes(root)[TAKE]) == \
            [f"{TAKE}.wav", f"{TAKE}_USB.wav"]


def test_copy_takes_links_inbox_takes_into_the_recording(tmp_path):
    inbox = tmp_path / "inbox"
    shutil.copytree(FIX / "card", inbox / TAKE)
    day = tmp_path / "day"
    day.mkdir()
    # bext says 22:08:21, so a recording named 22:08:45 owns it.
    (day / "2026-11-14 22-08-45.mkv").write_bytes(b"")
    (day / "2026-11-14 18-00-00.mkv").write_bytes(b"")
    lines = []
    matched = card.copy_takes(day, inbox, out=lines.append)
    assert matched == {TAKE: "2026-11-14 22-08-45"}
    got = sorted(p.name for p in (day / "2026-11-14 22-08-45" / "card").iterdir())
    assert got == sorted(p.name for p in (FIX / "card").iterdir())
    assert not (day / "2026-11-14 18-00-00").exists()
    assert len(list((inbox / TAKE).iterdir())) == 5      # inbox untouched
    lines.clear()
    card.copy_takes(day, inbox, out=lines.append)
    assert "0 file(s) linked or copied" in lines[0]


def test_copy_takes_without_an_inbox_does_nothing(tmp_path):
    (tmp_path / "2026-11-14 22-08-45.mkv").write_bytes(b"")
    assert card.copy_takes(tmp_path, tmp_path / "missing") == {}
    assert not (tmp_path / "2026-11-14 22-08-45").exists()
