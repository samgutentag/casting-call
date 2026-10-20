"""
obslog — tell whether OBS was in Safe Mode while a recording was made.

After a crash OBS offers to relaunch in Safe Mode, which skips third-party
modules, obs-websocket among them. The Stream Deck plays marker clips through
that websocket, so a Safe Mode call records audio and video normally and loses
every press without any sign in the file. On 2026-10-20 that cost a whole call's
markers. The only trace is OBS's own log, so ripa reads it.

Usage:
    python3 -m casting_call.obslog <recording.mkv> [--logs <dir>]
Prints a one-line warning when the recording's session ran in Safe Mode,
nothing otherwise.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

LOGS_DIR = Path.home() / 'Library/Application Support/obs-studio/logs'
SAFE_MARKERS = ('Safe Mode enabled', "Skipping module 'obs-websocket'")


def log_started(path):
    """A log's session start, from OBS's 'YYYY-MM-DD HH-MM-SS.txt' name, or None."""
    try:
        return datetime.strptime(Path(path).stem, '%Y-%m-%d %H-%M-%S')
    except ValueError:
        return None


def find_log(logs_dir, rec_start):
    """The newest log whose session began at or before rec_start, or None."""
    best = None
    for p in Path(logs_dir).glob('*.txt'):
        started = log_started(p)
        if started and started <= rec_start and (best is None or started > best[0]):
            best = (started, p)
    return best[1] if best else None


def safe_mode(text):
    return any(m in text for m in SAFE_MARKERS)


def recording_start(video):
    """When OBS started writing the file: its mtime minus its duration."""
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0',
         str(video)], capture_output=True, text=True, check=True).stdout.strip()
    end = datetime.fromtimestamp(Path(video).stat().st_mtime)
    return end - timedelta(seconds=float(out))


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description='Warn if a recording was made in OBS Safe Mode.')
    ap.add_argument('video')
    ap.add_argument('--logs', default=str(LOGS_DIR))
    args = ap.parse_args(argv)

    try:
        start = recording_start(args.video)
    except (subprocess.CalledProcessError, ValueError, OSError) as err:
        print(f'could not date the recording, OBS log not checked ({err})')
        return 0
    if not Path(args.logs).is_dir():
        return 0
    log = find_log(args.logs, start)
    if log is None:
        return 0
    try:
        text = log.read_text(errors='replace')
    except OSError as err:
        print(f'could not read {log.name}, OBS log not checked ({err})')
        return 0
    if safe_mode(text):
        print(f'OBS was in Safe Mode for this recording ({log.name}): obs-websocket was off, '
              'so Stream Deck presses could not reach OBS. Relaunch OBS normally before the next call.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
