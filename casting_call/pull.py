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
file that looks complete. The card is only read: shutil.copyfile copies data
alone, so no AppleDouble or other metadata is written anywhere on the card.

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


def copy_checked(src, target):
    """Copy via a .partial name, check the size, then rename. Raises OSError on a mismatch."""
    target = Path(target)
    partial = target.with_name(f".{target.name}.partial")
    try:
        shutil.copyfile(src, partial)
        want, got = os.stat(src).st_size, os.stat(partial).st_size
        if want != got:
            raise OSError(f"size mismatch for {src.name}: card {want}, copy {got}")
        os.replace(partial, target)
    finally:
        if partial.exists():
            partial.unlink()


def _mb(n):
    return f"{n / 1e6:.1f} MB"


def pull_takes(volume, inbox, all_inputs=False, out=print):
    """Copy every take not already in the inbox. Returns the number of failed files."""
    start = time.monotonic()
    takes = card.card_takes(volume)
    failures, total = 0, 0
    if not takes:
        out(f"no takes found on {volume}")
    for take in sorted(takes):
        dest = Path(inbox) / take
        todo = plan_take(takes[take], dest, all_inputs)
        if not todo:
            out(f"  {take}: already pulled, skipped")
            continue
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            out(f"  {take}: cannot create {dest}: {e}")
            failures += len(todo)
            continue
        size = 0
        for src, target in todo:
            try:
                copy_checked(src, target)
                size += target.stat().st_size
            except OSError as e:
                out(f"  {take}: FAILED {src.name}: {e}")
                failures += 1
        total += size
        out(f"  {take}: {len(todo)} file(s), {_mb(size)}")
    out(f"pulled {_mb(total)} in {time.monotonic() - start:.1f} s into {inbox}")
    return failures


def main(argv=None):
    ap = argparse.ArgumentParser(description="Copy new P4next takes into the local inbox.")
    ap.add_argument("volume")
    ap.add_argument("inbox")
    ap.add_argument("--all-inputs", action="store_true",
                    help="also copy MIC2 to MIC4 (unused, silent inputs)")
    args = ap.parse_args(argv)
    if not Path(args.volume).is_dir():
        print(f"card not mounted at {args.volume}", file=sys.stderr)
        return 2
    return 1 if pull_takes(args.volume, args.inbox, args.all_inputs) else 0


if __name__ == "__main__":
    sys.exit(main())
