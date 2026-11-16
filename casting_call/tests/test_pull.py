import shutil
from pathlib import Path

from casting_call import pull

FIX = Path(__file__).parent / "fixtures" / "p4next" / "card"
TAKE = "261114_220657"


def _fake_card(root):
    """A card laid out like the P4next writes it, from the fixture files."""
    multi = root / "P4next_Multitrack" / TAKE
    multi.mkdir(parents=True)
    (root / "P4next_SoundPad").mkdir()
    shutil.copy(FIX / f"{TAKE}.wav", root)
    for suffix in ("_MIC1", "_MIC2", "_USB", "_SOUND_PAD"):
        shutil.copy(FIX / f"{TAKE}{suffix}.wav", multi)
    return root


def _snapshot(root):
    return sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime)
                  for p in root.rglob("*"))


def test_pull_copies_mix_next_to_multitrack_and_skips_unused_inputs(tmp_path):
    vol = _fake_card(tmp_path / "card")
    before = _snapshot(vol)
    lines = []
    assert pull.pull_takes(vol, tmp_path / "inbox", out=lines.append) == 0
    got = sorted(p.name for p in (tmp_path / "inbox" / TAKE).iterdir())
    assert got == sorted(f"{TAKE}{s}.wav" for s in ("", "_MIC1", "_USB", "_SOUND_PAD"))
    assert _snapshot(vol) == before          # the card is never changed
    assert any("4 file(s)" in l for l in lines)


def test_second_pull_skips_takes_with_matching_sizes(tmp_path):
    vol = _fake_card(tmp_path / "card")
    inbox = tmp_path / "inbox"
    pull.pull_takes(vol, inbox, out=lambda s: None)
    lines = []
    assert pull.pull_takes(vol, inbox, out=lines.append) == 0
    assert any("pulled before" in l for l in lines)


def test_wrong_size_copy_is_pulled_again(tmp_path):
    vol = _fake_card(tmp_path / "card")
    dest = tmp_path / "inbox" / TAKE
    dest.mkdir(parents=True)
    (dest / f"{TAKE}_USB.wav").write_bytes(b"half")
    todo = pull.plan_take(list(vol.rglob("*.wav")), dest)
    assert f"{TAKE}_USB.wav" in [t.name for _s, t in todo]
    assert f"{TAKE}_MIC2.wav" not in [t.name for _s, t in todo]
    all_todo = pull.plan_take(list(vol.rglob("*.wav")), dest, all_inputs=True)
    assert f"{TAKE}_MIC2.wav" in [t.name for _s, t in all_todo]


def test_size_mismatch_leaves_no_file_behind(tmp_path, monkeypatch):
    src = FIX / f"{TAKE}_USB.wav"
    target = tmp_path / f"{TAKE}_USB.wav"

    def short_copy(a, b):
        Path(b).write_bytes(b"x")
    monkeypatch.setattr(pull.shutil, "copyfile", short_copy)
    try:
        pull.copy_checked(src, target)
        raise AssertionError("expected a size mismatch")
    except OSError as e:
        assert "size mismatch" in str(e)
    assert list(tmp_path.iterdir()) == []


def test_failed_copy_is_counted(tmp_path, monkeypatch):
    vol = _fake_card(tmp_path / "card")

    def boom(a, b):
        raise OSError("card pulled out")
    monkeypatch.setattr(pull.shutil, "copyfile", boom)
    assert pull.pull_takes(vol, tmp_path / "inbox", out=lambda s: None) == 4


def test_unmounted_card_exits_nonzero(tmp_path):
    assert pull.main([str(tmp_path / "missing"), str(tmp_path / "inbox")]) == 2


def test_take_moved_out_of_inbox_is_not_pulled_again(tmp_path):
    vol = _fake_card(tmp_path / "card")
    inbox = tmp_path / "inbox"
    pull.pull_takes(vol, inbox, out=lambda s: None)
    shutil.move(str(inbox / TAKE), str(tmp_path / "call.card"))
    lines = []
    assert pull.pull_takes(vol, inbox, out=lines.append) == 0
    assert not (inbox / TAKE).exists()
    assert any("pulled before" in l for l in lines)


def test_again_ignores_the_ledger(tmp_path):
    vol = _fake_card(tmp_path / "card")
    inbox = tmp_path / "inbox"
    pull.pull_takes(vol, inbox, out=lambda s: None)
    shutil.rmtree(inbox / TAKE)
    assert pull.pull_takes(vol, inbox, out=lambda s: None, again=True) == 0
    assert (inbox / TAKE / f"{TAKE}_MIC1.wav").exists()
