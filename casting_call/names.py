"""
names — put real names on far-side transcript lines from Meet's captions alone.

Every remote participant lands on the far-side track as one `[Caller]`. Meet
draws a speaker name above each caption turn, so the video already says who
was talking when. This reads those names without a roster: OCR a frame every
2 s, fold the spellings OCR produces for one person into one name, stitch the
caption words with the time each first appeared, then let the words falling
inside each `[Caller]` line vote for who said it.

A line is renamed only on a clear majority. Anything less stays `[Caller]`,
which the report already handles, so a wrong name is never traded for a
missing one.

Writes parts/pre-names.txt (the transcript as it was) and
parts/speaker-names.json (the names, their spellings, and every line's votes).

Usage:
    python3 -m casting_call.names <recording.mp4> <transcript.txt> --parts <dir>
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from .roster import Roster
from .transcript import (
    LINE_RE, SELF_LABEL, is_far_side, parse_transcript, render_transcript,
)

FPS = 0.5             # a caption turn stays on screen for many seconds; 2 s misses none
CALLER_LABEL = 'Caller'
FOLD_RATIO = 0.8      # spellings at least this similar are one person
MIN_FRAMES = 2        # a name seen in one frame only is a misread caption line
# Captions trail the audio. Tuned on the 2026-10-08 call against Deepgram
# diarization: 2.5 s lined caption words up with utterances best (backup.py
# uses 1.5 s, which is about splicing text, not matching turns).
LAG = 2.5
PAD = 1.0             # seconds either side of a line that still count as its words
MAX_LINE = 12         # a line with no next line still ends this many seconds on
MIN_VOTES = 5         # caption words a line needs before a vote means anything
MAJORITY = 0.7        # share of those words one name needs to win the line


# --- labels -> people -------------------------------------------------------

def _norm(name):
    return re.sub(r'[^a-z ]', '', name.lower()).strip()


def similarity(a, b):
    """How likely two caption labels are the same person, 0..1."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    wa, wb = set(a.split()), set(b.split())
    if wa <= wb or wb <= wa:
        return 1.0          # "Corey" and "Corey Weathers"
    if a in b or b in a:
        return 0.95         # "orey Weathers", a clipped first letter
    return SequenceMatcher(None, a, b).ratio()


def clean_label(text):
    """A caption label as it can sit inside [brackets] in the transcript."""
    return re.sub(r'\s+', ' ', re.sub(r'[\[\]|]', ' ', text)).strip()


def fold_names(frame_counts):
    """{label: frames seen} -> {label: canonical spelling}.

    The most common spelling of a person goes first and absorbs every later
    label it matches, so the canonical name is the one OCR read most often.
    """
    canon = {}
    heads = []
    for label, _ in sorted(frame_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        hit = next((h for h in heads if similarity(label, h) >= FOLD_RATIO), None)
        if hit is None:
            heads.append(label)
        canon[label] = hit or label
    return canon


def find_people(frames, min_frames=MIN_FRAMES):
    """From [(t, lines, found)], return (canon, people, spellings).

    canon maps every label read to its person (or to SELF_LABEL); people are
    the far-side names seen in at least min_frames frames, most seen first;
    spellings maps each person to {spelling: frames}.
    """
    counts = Counter()
    for _, lines, _ in frames:
        counts.update({clean_label(text) for text, is_label in lines if is_label})
    counts.pop('', None)
    canon = {lab: SELF_LABEL for lab in counts if _norm(lab) == 'you'}
    canon.update(fold_names({lab: n for lab, n in counts.items() if lab not in canon}))

    spellings = defaultdict(dict)
    for lab, person in canon.items():
        spellings[person][lab] = counts[lab]
    seen = {p: sum(v.values()) for p, v in spellings.items()}
    people = [p for p in sorted(seen, key=lambda p: -seen[p])
              if p != SELF_LABEL and is_far_side(p) and seen[p] >= min_frames]
    return canon, people, {p: spellings[p] for p in people}


# --- frames -> timed caption words -----------------------------------------

def caption_words(frames, canon, people, lag=LAG):
    """[(t, speaker)] for every caption word, t = first seen minus lag.

    Labels for dropped names are removed, so their words fall to whoever spoke
    before; they were a misread caption line, not a turn.
    """
    from .backup import frame_tokens
    from .stitch import new_tail_start

    keep = set(people) | {SELF_LABEL}
    roster = Roster(self_name=SELF_LABEL, members=[])
    committed = []
    for i, (t, lines, _) in enumerate(frames):
        mapped = []
        for text, is_label in lines:
            if is_label:
                person = canon.get(clean_label(text))
                if person not in keep:
                    continue
                text = person
            mapped.append((text, is_label))
        toks = frame_tokens(mapped, roster, keep_last=(i == len(frames) - 1))
        if not toks:
            continue
        start = new_tail_start([tok for tok, _ in committed], toks)
        committed.extend((tok, t) for tok in toks[start:])

    words, speaker = [], None
    for tok, t in committed:
        if isinstance(tok, tuple):
            speaker = tok[1]
        elif speaker:
            words.append((t - lag, speaker))
    return words


# --- caption words -> transcript labels ------------------------------------

def line_spans(entries, caller_label=CALLER_LABEL, max_line=MAX_LINE):
    """(start, end) for each caller line: it runs until the next caller line."""
    times = [e['t'] for e in entries if e['label'] == caller_label]
    spans = []
    for i, t in enumerate(times):
        nxt = times[i + 1] if i + 1 < len(times) else t + max_line
        spans.append((t, max(t + 1, min(nxt, t + max_line))))
    return spans


def vote(words, start, end, pad=PAD):
    """Far-side caption words falling in [start - pad, end + pad], by name."""
    return Counter(s for t, s in words if start - pad <= t <= end + pad and s != SELF_LABEL)


def pick(votes, min_votes=MIN_VOTES, majority=MAJORITY):
    """The winning name, or None when no name holds a clear majority."""
    total = sum(votes.values())
    if total < min_votes:
        return None
    name, n = votes.most_common(1)[0]
    return name if n / total >= majority else None


def name_lines(entries, words, people, caller_label=CALLER_LABEL):
    """Relabel caller lines. Returns (entries, per-line records)."""
    spans = iter(line_spans(entries, caller_label))
    single = people[0] if len(people) == 1 else None
    out, records = [], []
    for e in entries:
        if e['label'] != caller_label:
            out.append(e)
            continue
        start, end = next(spans)
        votes = vote(words, start, end)
        name = single or pick(votes)
        records.append({'t': e['t'], 'end': end, 'votes': dict(votes.most_common()),
                        'name': name, 'text': e['text']})
        out.append(dict(e, label=name) if name else e)
    return out, records


# --- CLI --------------------------------------------------------------------

def _hms(sec):
    sec = int(sec)
    return f'{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}'


def _duration(video):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                          '-of', 'csv=p=0', str(video)], capture_output=True, text=True)
    return float(out.stdout.strip())


def load_frames(path):
    data = json.loads(Path(path).read_text())
    return [(f['t'], [tuple(line) for line in f['lines']], f['found']) for f in data['frames']]


def save_frames(path, frames):
    Path(path).write_text(json.dumps({'frames': [
        {'t': t, 'found': found, 'lines': lines} for t, lines, found in frames]}))


def main(argv=None):
    import argparse
    import time

    ap = argparse.ArgumentParser(description='Name far-side lines from Meet captions.')
    ap.add_argument('video')
    ap.add_argument('transcript')
    ap.add_argument('--parts', required=True, help='where pre-names.txt and speaker-names.json go')
    ap.add_argument('--caller-label', default=CALLER_LABEL)
    ap.add_argument('--fps', type=float, default=FPS)
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--frames', help='OCR cache: read it if present, else write it')
    args = ap.parse_args(argv)

    txt, parts = Path(args.transcript), Path(args.parts)
    pre = parts / 'pre-names.txt'
    # A rerun starts from the unnamed transcript, not from its own output.
    source = pre if pre.exists() else txt
    entries = parse_transcript(source.read_text(encoding='utf-8'))
    n_caller = sum(e['label'] == args.caller_label for e in entries)
    if not n_caller:
        print(f'no [{args.caller_label}] lines, nothing to name')
        return 0

    began = time.time()
    if args.frames and Path(args.frames).exists():
        frames = load_frames(args.frames)
    else:
        if not shutil.which('tesseract'):
            print('caption names skipped: tesseract not found (brew install tesseract)')
            return 0
        try:
            from .backup import ocr_frames
            frames = ocr_frames(args.video, 0.0, _duration(args.video), args.fps, args.jobs)
        except (ImportError, OSError, ValueError, subprocess.CalledProcessError) as err:
            print(f'caption names skipped: could not read the video ({err})')
            return 0
        if args.frames:
            save_frames(args.frames, frames)

    anchored = sum(found for _, _, found in frames)
    if not anchored:
        print(f'caption names skipped: no Meet on screen, lines stay [{args.caller_label}]')
        return 0

    canon, people, spellings = find_people(frames)
    if not people:
        print(f'caption names: Meet on screen in {anchored}/{len(frames)} frames but no '
              f'far-side caption names, lines stay [{args.caller_label}]')
        return 0
    words = caption_words(frames, canon, people)
    named, records = name_lines(entries, words, people, args.caller_label)

    parts.mkdir(parents=True, exist_ok=True)
    if not pre.exists():
        shutil.copyfile(txt, pre)
    renamed = Counter(r['name'] for r in records if r['name'])
    (parts / 'speaker-names.json').write_text(json.dumps({
        'names': {p: {'lines': renamed.get(p, 0), 'spellings': spellings[p]} for p in people},
        'single_name_call': len(people) == 1,
        'frames': len(frames), 'frames_with_meet': anchored,
        'lines': [dict(r, tstr=_hms(r['t'])) for r in records],
    }, indent=1) + '\n')
    # Lines that are not transcript lines (the card header) stay on top.
    head = ''.join(ln for ln in txt.read_text(encoding='utf-8').splitlines(keepends=True)
                   if not LINE_RE.match(ln.rstrip('\n')))
    txt.write_text(head + render_transcript(named), encoding='utf-8')

    left = n_caller - sum(renamed.values())
    how = ' (one far-side name, all lines)' if len(people) == 1 else ''
    print(f'caption names: {", ".join(people)}{how}')
    print(f'  renamed {sum(renamed.values())}/{n_caller} [{args.caller_label}] lines: '
          + ', '.join(f'{p} {n}' for p, n in renamed.most_common()))
    print(f'  left as [{args.caller_label}]: {left}  '
          f'({len(frames)} frames, {anchored} with Meet, {time.time() - began:.0f}s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
