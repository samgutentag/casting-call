import builtins
import json
import time
import types

from casting_call import parallel, ui


def test_both_tasks_succeed_and_run_at_the_same_time():
    def slow(v):
        def fn():
            time.sleep(0.3)
            return v, None
        return fn
    began = time.monotonic()
    res = parallel.run_parallel({"you": slow(3), "caller": slow(5)})
    assert time.monotonic() - began < 0.55        # concurrent, not 0.6 s back to back
    assert res["you"] == {"ok": True, "value": 3, "reason": None, "seconds": res["you"]["seconds"]}
    assert res["caller"]["value"] == 5


def test_one_track_failing_keeps_the_other():
    res = parallel.run_parallel({"you": lambda: (12, None), "caller": lambda: (None, "HTTP 401")})
    assert res["you"]["ok"] and res["you"]["value"] == 12
    assert not res["caller"]["ok"] and res["caller"]["reason"] == "HTTP 401"


def test_a_crash_in_one_task_does_not_lose_the_others():
    def boom():
        raise RuntimeError("ocr exploded")
    res = parallel.run_parallel({"ocr": boom, "you": lambda: (4, None), "caller": lambda: (6, None)})
    assert not res["ocr"]["ok"] and "ocr exploded" in res["ocr"]["reason"]
    assert res["you"]["value"] == 4 and res["caller"]["value"] == 6


def test_deepgram_command_writes_status_and_falls_back_per_track(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RIPA_PLAIN", "1")
    calls = {}

    def fake(wav, srt, raw_out=None, diarize=False, speakers_out=None):
        calls[wav] = (diarize, speakers_out)
        return (None, "timeout") if wav == "caller.wav" else (7, None)

    args = types.SimpleNamespace(parts=str(tmp_path), status=str(tmp_path / "s.json"),
                                 you="you.wav", you_srt="you.srt",
                                 caller="caller.wav", caller_srt="caller.srt")
    assert parallel.cmd_deepgram(args, transcribe=fake) == 0
    status = json.loads((tmp_path / "s.json").read_text())
    assert status["you"]["ok"] and status["you"]["value"] == 7
    assert status["caller"]["reason"] == "timeout"
    assert calls["you.wav"][0] is False                 # only the far side is diarized
    assert calls["caller.wav"][0] is True
    out = capsys.readouterr().out
    assert "✓ You: Deepgram, 7 lines" in out and "⚠⚠ Caller: Deepgram failed (timeout)" in out


def test_ocr_without_tesseract_fails_cleanly_and_writes_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(parallel.shutil, "which", lambda name: None)
    args = types.SimpleNamespace(video="v.mkv", frames=str(tmp_path / "f.json"), fps=None, jobs=2)
    assert parallel.cmd_ocr(args) == 3
    assert not (tmp_path / "f.json").exists()


def test_ui_is_plain_when_rich_is_missing(monkeypatch, capsys):
    monkeypatch.delenv("RIPA_PLAIN", raising=False)
    real_import = builtins.__import__

    def no_rich(name, *a, **k):
        if name == "rich" or name.startswith("rich."):
            raise ImportError("no rich here")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_rich)
    assert not ui.fancy()
    ui.summary([("transcribe", "ok", 12.0, "you: deepgram"), ("caption names", "warn", 3.0, "")],
               next_cmd="/process-transcript /x")
    out = capsys.readouterr().out
    assert "ok      transcribe" in out and "warn    caption names" in out
    assert "Next: /process-transcript /x" in out


def test_ripa_plain_forces_plain(monkeypatch):
    monkeypatch.setenv("RIPA_PLAIN", "1")
    assert not ui.fancy()


def test_progress_bar_falls_back_to_the_plain_bar(monkeypatch):
    monkeypatch.setenv("RIPA_PLAIN", "1")
    made = []
    bar = ui.progress_bar(100, lambda total: made.append(total) or "plain")
    assert bar == "plain" and made == [100]
