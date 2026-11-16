"""
parallel — run ripa's slow, independent steps at the same time.

Two jobs, both called from ripa:

    deepgram  sends the you and caller tracks to Deepgram concurrently. Each
              track succeeds or fails on its own; a failed track is reported,
              and ripa falls back to whisper for that track only.
    ocr       reads Meet's caption frames for the whole recording into a cache
              (parts/caption-frames.json). ripa starts it in the background
              before extracting audio, so caption reading overlaps the demux
              and the transcription instead of following them. The naming step
              reads the cache instead of the video.

Usage (from ripa):
    python3 -m casting_call.parallel deepgram --parts <dir> --status <out.json> \\
        [--you <wav> --you-srt <base.srt>] [--caller <wav> --caller-srt <base.srt>]
    python3 -m casting_call.parallel ocr <video> --frames <cache.json> [--fps F] [--jobs N]

Exit codes: 0 when every job finished (a failed track is still exit 0, its
status says why), 2 bad usage, 3 the ocr job could not read the video.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from casting_call import ui


def run_parallel(tasks, on_done=None):
    """Run {name: callable} at once. Returns {name: {ok, value, reason, seconds}}.

    A callable returns (value, reason): reason None means success. An exception
    is a failure for that task only; the others still finish.
    """
    results = {}

    def wrap(name, fn):
        began = time.monotonic()
        try:
            value, reason = fn()
            res = {"ok": reason is None, "value": value, "reason": reason}
        except Exception as err:  # one track's crash must not lose the other's transcript
            res = {"ok": False, "value": None, "reason": f"{type(err).__name__}: {err}"}
        res["seconds"] = round(time.monotonic() - began, 1)
        return name, res

    if not tasks:
        return results
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = [pool.submit(wrap, n, fn) for n, fn in tasks.items()]
        for fut in futures:
            name, res = fut.result()
            results[name] = res
            if on_done:
                on_done(name, res)
    return results


def deepgram_tasks(args, transcribe=None):
    if transcribe is None:
        from casting_call.deepgram import transcribe
    parts = Path(args.parts)
    tasks = {}
    if args.you:
        tasks["you"] = lambda: transcribe(
            args.you, args.you_srt, raw_out=parts / "you.deepgram.json")
    if args.caller:
        tasks["caller"] = lambda: transcribe(
            args.caller, args.caller_srt, raw_out=parts / "caller.deepgram.json",
            diarize=True, speakers_out=parts / "caller.speakers.json")
    return tasks


def cmd_deepgram(args, transcribe=None):
    tasks = deepgram_tasks(args, transcribe)
    labels = {"you": "You", "caller": "Caller"}
    with ui.Status([labels[n] for n in tasks], "sending to Deepgram") as status:
        results = run_parallel(
            tasks, on_done=lambda n, r: status.done(labels[n], r["ok"],
                                                    f'{r["value"]} lines' if r["ok"] else r["reason"],
                                                    r["seconds"]))
    Path(args.status).write_text(json.dumps(results, indent=1, default=str) + "\n")
    return 0


def cmd_ocr(args):
    from casting_call.names import FPS, _duration, save_frames
    if not shutil.which("tesseract"):
        print("caption reading skipped: tesseract not found")
        return 3
    began = time.monotonic()
    try:
        from casting_call.backup import ocr_frames
        frames = ocr_frames(args.video, 0.0, _duration(args.video), args.fps or FPS, args.jobs)
    except (ImportError, OSError, ValueError, subprocess.CalledProcessError) as err:
        print(f"caption reading failed: {err}")
        return 3
    tmp = Path(args.frames).with_suffix(".partial")
    save_frames(tmp, frames)
    tmp.replace(args.frames)
    anchored = sum(found for _, _, found in frames)
    print(f"caption frames: {len(frames)} read, {anchored} with Meet, "
          f"{time.monotonic() - began:.0f}s")
    return 0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Run ripa's independent steps at the same time.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    dg = sub.add_parser("deepgram")
    dg.add_argument("--parts", required=True)
    dg.add_argument("--status", required=True)
    dg.add_argument("--you")
    dg.add_argument("--you-srt")
    dg.add_argument("--caller")
    dg.add_argument("--caller-srt")
    oc = sub.add_parser("ocr")
    oc.add_argument("video")
    oc.add_argument("--frames", required=True)
    oc.add_argument("--fps", type=float)
    oc.add_argument("--jobs", type=int, default=8)
    args = ap.parse_args(argv)
    if args.cmd == "deepgram":
        if (args.you and not args.you_srt) or (args.caller and not args.caller_srt):
            ap.error("each track needs its --*-srt output")
        return cmd_deepgram(args)
    return cmd_ocr(args)


if __name__ == "__main__":
    sys.exit(main())
