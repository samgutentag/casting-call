"""
ui — optional styled terminal output for ripa and ripcard.

When rich imports, stdout is a terminal and RIPA_PLAIN is not 1, status lines,
progress bars and the end-of-run summary are drawn with rich. Otherwise every
function prints the same plain text ripa always printed. Nothing here is
required: a machine without rich gets plain text, never an error.

Colors are Okabe-Ito, chosen for colorblind readers, and every status also says
ok, warn or failed in words, so no state is carried by color alone.

Usage (from ripa):
    python3 -m casting_call.ui summary <steps.tsv> [--next "<command>"]
    (steps.tsv: one "step<TAB>status<TAB>seconds<TAB>note" line per step)
"""

from __future__ import annotations

import os
import sys
import time

OK, WARN, FAILED, INFO, ACCENT = "#009E73", "#E69F00", "#D55E00", "#56B4E9", "#CC79A7"
STATUS_STYLE = {"ok": OK, "warn": WARN, "failed": FAILED, "skipped": INFO}


def _rich():
    if os.environ.get("RIPA_PLAIN") == "1":
        return None
    try:
        import rich  # noqa: F401
        from rich.console import Console
    except ImportError:
        return None
    from rich.theme import Theme
    # rich's defaults draw transfer speed in red, which reads as an error.
    console = Console(stderr=False, theme=Theme({
        "progress.download": OK, "progress.data.speed": INFO,
        "progress.remaining": INFO, "progress.percentage": "bold"}))
    return console if console.is_terminal else None


def fancy():
    return _rich() is not None


def fmt_secs(sec):
    sec = int(round(float(sec)))
    return f"{sec // 60}:{sec % 60:02d}"


class Status:
    """Concurrent tasks with one line each: a spinner while running, then
    'ok' or 'failed' with a note and the time it took."""

    def __init__(self, names, verb):
        self.names, self.verb = list(names), verb
        self.state = {n: None for n in self.names}
        self.console = _rich()
        self.live = None
        self.began = time.monotonic()

    def __enter__(self):
        if self.console:
            from rich.live import Live
            self.live = Live(self._render(), console=self.console, refresh_per_second=8,
                             transient=False)
            self.live.__enter__()
        else:
            for n in self.names:
                print(f"  → {n}: {self.verb}...", flush=True)
        return self

    def _render(self):
        from rich.spinner import Spinner
        from rich.table import Table
        from rich.text import Text
        t = Table.grid(padding=(0, 2))
        for n in self.names:
            st = self.state[n]
            if st is None:
                t.add_row(Spinner("dots", style=INFO), Text(n, style="bold"),
                          Text(f"{self.verb}... {fmt_secs(time.monotonic() - self.began)}",
                               style=INFO))
            else:
                ok, note, secs = st
                word, color = ("ok", OK) if ok else ("failed", FAILED)
                t.add_row(Text(word, style=f"bold {color}"), Text(n, style="bold"),
                          Text(f"{note}  ({fmt_secs(secs)})"))
        return t

    def done(self, name, ok, note, seconds):
        self.state[name] = (ok, note, seconds)
        if self.live:
            self.live.update(self._render())
        elif ok:
            print(f"  ✓ {name}: Deepgram, {note} ({fmt_secs(seconds)})", flush=True)
        else:
            print(f"  ⚠⚠ {name}: Deepgram failed ({note})", flush=True)

    def __exit__(self, *exc):
        if self.live:
            self.live.update(self._render())
            self.live.__exit__(*exc)
        return False


class RichBar:
    """Same interface as pull.Bar, drawn with rich.Progress."""

    def __init__(self, total, console):
        from rich.progress import (BarColumn, DownloadColumn, Progress, TextColumn,
                                   TimeRemainingColumn, TransferSpeedColumn)
        self.progress = Progress(
            TextColumn("  [bold]{task.fields[name]}", justify="left"),
            BarColumn(complete_style=OK, finished_style=OK, pulse_style=INFO),
            TextColumn("{task.percentage:>3.0f}%"),
            DownloadColumn(), TransferSpeedColumn(), TimeRemainingColumn(),
            console=console, transient=True)
        self.base = 0
        self.progress.start()
        self.task = self.progress.add_task("pull", total=max(total, 1), name="")

    def file(self, name):
        self.progress.update(self.task, name=name[:30])

    def update(self, file_done):
        self.progress.update(self.task, completed=self.base + file_done)

    def finish_file(self, size):
        self.base += size
        self.progress.update(self.task, completed=self.base)

    def close(self):
        self.progress.stop()


def progress_bar(total, plain_factory):
    console = _rich()
    return RichBar(total, console) if console and total > 0 else plain_factory(total)


def read_steps(path):
    rows = []
    for raw in open(path, encoding="utf-8"):
        parts = raw.rstrip("\n").split("\t")
        if len(parts) >= 3:
            rows.append((parts[0], parts[1], float(parts[2] or 0), parts[3] if len(parts) > 3 else ""))
    return rows


def summary(rows, next_cmd=None, total=None, out=print):
    total = sum(r[2] for r in rows) if total is None else total
    console = _rich()
    if not console:
        out("Steps:")
        for step, status, secs, note in rows:
            out(f"  {status:<7} {step:<26} {fmt_secs(secs):>6}  {note}".rstrip())
        out(f"  total {fmt_secs(total)}")
        if next_cmd:
            out(f"Next: {next_cmd}")
        return
    from rich.table import Table
    from rich.text import Text
    t = Table(show_header=True, header_style=f"bold {INFO}", box=None, padding=(0, 2))
    t.add_column("status")
    t.add_column("step")
    t.add_column("time", justify="right")
    t.add_column("note")
    for step, status, secs, note in rows:
        t.add_row(Text(status, style=f"bold {STATUS_STYLE.get(status, INFO)}"), step,
                  fmt_secs(secs), note)
    t.add_row("", Text("total", style="bold"), Text(fmt_secs(total), style="bold"), "")
    console.print(t)
    if next_cmd:
        # Printed on one unwrapped line so it copies cleanly into Claude Code.
        console.print(Text("ok  ready for review: ", style=f"bold {OK}") + Text(next_cmd, style="bold"),
                      soft_wrap=True)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Styled output helpers for ripa.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sm = sub.add_parser("summary")
    sm.add_argument("steps")
    sm.add_argument("--next")
    sm.add_argument("--total", type=float)
    args = ap.parse_args(argv)
    try:
        rows = read_steps(args.steps)
    except OSError as err:
        print(f"no step timings ({err.strerror})")
        return 0
    summary(rows, args.next, args.total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
