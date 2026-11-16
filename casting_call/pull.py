"""
pull — copy new P4next takes off the mounted card into a local inbox, fast.

While the P4next is in USB File Transfer mode it stops being the Mac's audio
device, so the card has to be mounted for as short a time as possible. This
copies every take not already pulled and nothing else; ripa later reads the
inbox, never the card.

    <inbox>/<take>/<take>.wav          stereo mix (card root)
    <inbox>/<take>/<take>_MIC1.wav     multitrack files
    ...

MIC2 to MIC4 are digital silence on this rig and are skipped unless
all_inputs is set. Each file is copied to a hidden .partial name and only
renamed once its size matches the card, so an interrupted pull never leaves a
file that looks complete. The card is only read: files are copied as plain data,
so no AppleDouble or other metadata is written anywhere on the card.

Usage (from bin/pull_card.sh):
    python3 -m casting_call.pull <volume> <inbox> [--all-inputs]
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

from casting_call import card


LEDGER = ".pulled"


def read_ledger(inbox):
    """Takes already pulled once, so moving or deleting a take from the inbox
    does not make the next pull copy it again."""
    try:
        return {ln.strip() for ln in (Path(inbox) / LEDGER).read_text().splitlines() if ln.strip()}
    except FileNotFoundError:
        return set()


def add_to_ledger(inbox, take):
    with open(Path(inbox) / LEDGER, "a", encoding="utf-8") as f:
        f.write(take + "\n")


def plan_take(files, dest, all_inputs=False):
    """[(src, target)] still to copy for one take: wanted files whose inbox copy
    is missing or a different size."""
    todo = []
    for src in sorted(files):
        c = card.classify(src.name)
        if c is None or (c[1] == "ignored" and not all_inputs):
            continue
        target = Path(dest) / src.name
        try:
            if target.exists() and target.stat().st_size == src.stat().st_size:
                continue
        except OSError:
            pass
        todo.append((src, target))
    return todo


CHUNK = 4 * 1024 * 1024


def _copy_with_progress(src, dst, progress=None):
    """Plain data copy in chunks (nothing written back to the card), calling
    progress(bytes_done) as it goes."""
    done = 0
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            buf = fi.read(CHUNK)
            if not buf:
                break
            fo.write(buf)
            done += len(buf)
            if progress:
                progress(done)


class Bar:
    """One-line progress for a pull: the file, percent, MB, speed and time left
    for the whole pull. Only draws on a terminal, so logs stay clean."""

    def __init__(self, total, stream=sys.stderr):
        self.total, self.base, self.stream = max(total, 1), 0, stream
        self.start = time.monotonic()
        self.live = hasattr(stream, "isatty") and stream.isatty()
        self.name, self.last = "", 0.0

    def file(self, name):
        self.name = name

    def update(self, file_done):
        if not self.live:
            return
        now = time.monotonic()
        if now - self.last < 0.25:
            return
        self.last = now
        done = self.base + file_done
        rate = done / max(now - self.start, 1e-6)
        left = (self.total - done) / rate if rate > 0 else 0
        width = 30
        fill = int(width * done / self.total)
        self.stream.write(
            f"\r  [{'█' * fill}{'░' * (width - fill)}] {done / self.total:4.0%} "
            f"{done / 1e6:,.0f}/{self.total / 1e6:,.0f} MB  {rate / 1e6:4.1f} MB/s  "
            f"{int(left // 60)}:{int(left % 60):02d} left  {self.name[:28]:<28}")
        self.stream.flush()

    def finish_file(self, size):
        self.base += size

    def close(self):
        if self.live:
            self.stream.write("\r" + " " * 110 + "\r")
            self.stream.flush()


def copy_checked(src, target, progress=None):
    """Copy via a .partial name, check the size, then rename. Raises OSError on a mismatch."""
    target = Path(target)
    partial = target.with_name(f".{target.name}.partial")
    try:
        _copy_with_progress(src, partial, progress)
        want, got = os.stat(src).st_size, os.stat(partial).st_size
        if want != got:
            raise OSError(f"size mismatch for {src.name}: card {want}, copy {got}")
        os.replace(partial, target)
    finally:
        if partial.exists():
            partial.unlink()


def _mb(n):
    return f"{n / 1e6:.1f} MB"


def pull_takes(volume, inbox, all_inputs=False, out=print, again=False):
    """Copy every take not pulled before. Returns the number of failed files."""
    start = time.monotonic()
    takes = card.card_takes(volume)
    Path(inbox).mkdir(parents=True, exist_ok=True)
    done = set() if again else read_ledger(inbox)
    failures, total = 0, 0
    if not takes:
        out(f"no takes found on {volume}")
    plans = {}
    for take in sorted(takes):
        if take not in done:
            plans[take] = plan_take(takes[take], Path(inbox) / take, all_inputs)
    bar = Bar(sum(src.stat().st_size for todo in plans.values() for src, _ in todo))
    for take in sorted(takes):
        if take in done:
            out(f"  {take}: pulled before, skipped")
            continue
        dest = Path(inbox) / take
        todo = plans[take]
        if not todo:
            out(f"  {take}: already pulled, skipped")
            add_to_ledger(inbox, take)
            continue
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            out(f"  {take}: cannot create {dest}: {e}")
            failures += len(todo)
            continue
        size = 0
        for src, target in todo:
            bar.file(src.name)
            try:
                copy_checked(src, target, bar.update)
                size += target.stat().st_size
                bar.finish_file(target.stat().st_size)
            except OSError as e:
                bar.close()
                out(f"  {take}: FAILED {src.name}: {e}")
                failures += 1
        bar.close()
        total += size
        out(f"  {take}: {len(todo)} file(s), {_mb(size)}")
        if not any(not tg.exists() for _, tg in todo):
            add_to_ledger(inbox, take)
    out(f"pulled {_mb(total)} in {time.monotonic() - start:.1f} s into {inbox}")
    return failures


def main(argv=None):
    ap = argparse.ArgumentParser(description="Copy new P4next takes into the local inbox.")
    ap.add_argument("volume")
    ap.add_argument("inbox")
    ap.add_argument("--all-inputs", action="store_true",
                    help="also copy MIC2 to MIC4 (unused, silent inputs)")
    ap.add_argument("--again", action="store_true",
                    help="ignore the record of earlier pulls and copy every take again")
    args = ap.parse_args(argv)
    if not Path(args.volume).is_dir():
        print(f"card not mounted at {args.volume}", file=sys.stderr)
        return 2
    return 1 if pull_takes(args.volume, args.inbox, args.all_inputs, again=args.again) else 0


if __name__ == "__main__":
    sys.exit(main())
