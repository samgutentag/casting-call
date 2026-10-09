"""
backup — patch a far-side audio dropout with Google Meet's on-screen captions.

Track 3 (system audio) is the far side's only clean channel. When its capture
dies mid-call the words are gone from the audio, but Meet was drawing captions
the whole time, so the video still has them. This reads the captions over just
the silent window, confirms someone other than Sam was actually talking (a quiet
stretch can also be Sam presenting), and splices the caption text into the
transcript as [Caller] lines.

Captions are a backup, never a source: a call whose audio is intact never gets
here, and Sam's own words always come from his mic, not from his caption lines.

The Meet window moves between and during calls, so there is no fixed crop.
Each frame is anchored on the red end-call button (locate.find_hangup), a wide
box above it is OCR'd, and the caption column is picked out by word geometry.

Usage:
    python3 -m casting_call.backup <recording.mkv> <transcript.txt> \
        --windows "312.5:871.0,1500:1620" --parts <dir>
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from .config import Config
from .locate import HANGUP_WIDTH_4K, caption_search_box, find_hangup
from .roster import load_roster, match_name
from .stitch import (
    CONF, JUNK_RE, SPEAKER_MATCH_THRESHOLD, _clean_token, dedupe_stutter,
    is_gibberish, new_tail_start,
)
from .transcript import parse_transcript, render_transcript

FPS = 1.5                 # captions scroll slowly; matches ripcap's default
CHUNK_SECONDS = 120       # frames are extracted and deleted in chunks to cap disk use
MIN_OTHER_WORDS = 20      # caption words from someone else needed to call it a dropout
LINE_SECONDS = 6          # split a long caption turn into transcript lines about this long

# Caption geometry, in px at 4K (scaled by the end-call button's width).
BODY_MEDIAN_MIN = 24      # caption text; chat and tile labels OCR smaller
LABEL_MAX = 25            # tallest word in a speaker-name line (fallback, no pixels)

# Meet draws speaker names in grey and caption text in white. Measured on 4K
# recordings: names peak at 211-212, caption text at 255, chat at ~230, tile
# labels at 254. Brightness separates them far better than size does.
LABEL_GREY = (200, 222)
ALIGN_TOL = 25            # a caption line starts this close to the column edge
WORD_GAP = 80             # wider than this between words = a different panel


# --- one frame -> caption lines ------------------------------------------

def _words(tsv_text):
    """Confident OCR words as dicts, grouped by tesseract's line id."""
    rows = {}
    for ln in tsv_text.splitlines()[1:]:
        p = ln.split('\t')
        if len(p) < 12 or not p[11].strip():
            continue
        try:
            conf = float(p[10])
            left, top, width, height = (int(v) for v in p[6:10])
        except ValueError:
            continue
        if conf < CONF:
            continue
        rows.setdefault((p[2], p[3], p[4]), []).append(
            {'left': left, 'top': top, 'w': width, 'h': height, 'text': p[11].strip()})
    for words in rows.values():
        words.sort(key=lambda w: w['left'])
    return list(rows.values())


def _segments(words, gap):
    """Split one OCR row wherever the space between words exceeds `gap`."""
    segs, cur = [], []
    for w in words:
        if cur and w['left'] - (cur[-1]['left'] + cur[-1]['w']) > gap:
            segs.append(cur)
            cur = []
        cur.append(w)
    if cur:
        segs.append(cur)
    return segs


def _median(vals):
    vals = sorted(vals)
    return vals[len(vals) // 2]


def _is_grey(gray, w):
    box = gray[w['top']:w['top'] + w['h'], w['left']:w['left'] + w['w']]
    if box.size == 0:
        return False
    peak = float(np.percentile(box, 98))
    return LABEL_GREY[0] <= peak <= LABEL_GREY[1]


def caption_lines(tsv_text, u=1.0, gray=None):
    """[(text, is_label)] for the caption column, top to bottom.

    psm 6 reads the wide crop as one block, so a caption line and a chat bubble
    on the same row come back as one OCR line. Rows are split at wide gaps, the
    caption column's left edge is the most common start of body-sized segments,
    and only segments starting on that edge survive. That drops chat, tile name
    labels, avatars and neighboring windows without knowing where any of them are.
    """
    rows = [_segments(ws, WORD_GAP * u) for ws in _words(tsv_text)]
    starts = Counter()
    for segs in rows:
        for seg in segs:
            if len(seg) >= 3 and _median([w['h'] for w in seg]) >= BODY_MEDIAN_MIN * u:
                starts[round(seg[0]['left'] / (10 * u))] += 1
    if not starts:
        return []
    edge = starts.most_common(1)[0][0] * 10 * u

    out = []
    for segs in rows:
        # Avatars sit just left of the edge and would drag a label's start off it.
        words = [w for seg in segs for w in seg if w['left'] >= edge - 10 * u]
        segs = _segments(words, WORD_GAP * u)
        if not segs or abs(segs[0][0]['left'] - edge) > ALIGN_TOL * u:
            continue
        seg = segs[0]
        text = ' '.join(w['text'] for w in seg)
        if len(re.sub(r'[^a-zA-Z]', '', text)) < 2 or JUNK_RE.search(text) or is_gibberish(text):
            continue
        if gray is not None:
            # An avatar's edge can OCR as a stray "a" or "@" in front of a name.
            while len(seg) > 1 and len(seg[0]['text']) <= 2 and not _is_grey(gray, seg[0]):
                seg = seg[1:]
            is_label = len(seg) <= 4 and all(_is_grey(gray, w) for w in seg)
            if is_label:
                text = ' '.join(w['text'] for w in seg)
        else:
            is_label = len(seg) <= 4 and max(w['h'] for w in seg) <= LABEL_MAX * u
        out.append((seg[0]['top'], text, is_label))
    out.sort(key=lambda r: r[0])
    # A name is always followed by what that person said. A short reply like
    # "Yes." can OCR as small as a name, so a "label" with no text under it is text.
    return [(text, is_label and i + 1 < len(out) and not out[i + 1][2])
            for i, (_, text, is_label) in enumerate(out)]


def label_name(text, roster):
    """Canonical name for a caption speaker label: roster match, else as read."""
    return match_name(text, roster, SPEAKER_MATCH_THRESHOLD) or text.strip(' |')


def frame_tokens(lines, roster, keep_last=False):
    """Caption lines -> tokens with inline ('SPK', name) markers.

    The bottom line is Meet's interim result and is dropped until it scrolls up
    and settles, the same rule stitch.frame_tokens uses.
    """
    if not keep_last and len(lines) > 1:
        lines = lines[:-1]
    toks = []
    for text, is_label in lines:
        if is_label:
            toks.append(('SPK', label_name(text, roster)))
        else:
            toks.extend(text.split())
    return toks


# --- frames over time -> timed caption lines ------------------------------

def stitch_timed(frames, roster):
    """[(t, tokens)] per frame -> [(t, speaker, text)] caption lines.

    Each token keeps the time of the frame that first committed it, so the
    lines can be spliced into a transcript by timestamp. Long turns are cut
    into lines of about LINE_SECONDS to match whisper's line length.
    """
    committed = []           # [(token, t)]
    for t, toks in frames:
        if not toks:
            continue
        start = new_tail_start([tok for tok, _ in committed], toks)
        committed.extend((tok, t) for tok in toks[start:])

    for idx, (tok, _) in enumerate(committed):
        if isinstance(tok, tuple):
            committed = committed[idx:]
            break
    committed = [(_clean_token(tok), t) for tok, t in committed]
    committed = dedupe_stutter(committed, key=lambda pair: pair[0])

    lines, speaker, buf = [], None, []

    def flush():
        if buf and speaker:
            lines.append((buf[0][1], speaker, ' '.join(tok for tok, _ in buf)))
        buf.clear()

    for tok, t in committed:
        if isinstance(tok, tuple):
            if tok[1] != speaker:
                flush()
                speaker = tok[1]
            continue
        if buf and t - buf[0][1] >= LINE_SECONDS and re.search(r'[.?!]$', buf[-1][0]):
            flush()
        buf.append((tok, t))
    flush()
    return lines


def other_words(lines, self_name):
    """How many caption words came from someone other than Sam."""
    return sum(len(text.split()) for _, who, text in lines if who != self_name)


# --- transcript splice -----------------------------------------------------

def splice(entries, windows, lines, self_name, caller_label='Caller', lag=0.0):
    """Replace far-side lines inside each window with caption lines.

    Only other people's captions go in: Sam's mic already has his words, and
    his caption lines would duplicate them. `lag` shifts caption times back,
    since captions trail the audio they transcribe.
    """
    def inside(t):
        return any(s <= t < e for s, e in windows)

    kept = [e for e in entries if not (e['label'] == caller_label and inside(e['t']))]
    added = []
    for t, who, text in lines:
        t = max(0.0, t - lag)
        if who != self_name and inside(t):
            added.append({'t': int(t), 'label': caller_label, 'text': text})
    return sorted(kept + added, key=lambda e: e['t']), len(added)


# --- video -> frames -------------------------------------------------------

def _ocr_frame(path):
    """(lines, found) for one full frame on disk. found=False when Meet's
    end-call button is not on screen (window hidden, another app in front)."""
    with Image.open(path) as img:
        anchor = find_hangup(img)
        if anchor is None:
            return [], False
        box = caption_search_box(img, anchor)
        crop_path = os.path.splitext(path)[0] + '-crop.png'
        crop = img.crop(box)
        crop.save(crop_path)
        gray = np.asarray(crop.convert('L'))
    tsv = subprocess.run(['tesseract', crop_path, '-', '--psm', '6', 'tsv'],
                         capture_output=True, text=True).stdout
    return caption_lines(tsv, anchor[2] / HANGUP_WIDTH_4K, gray), True


def read_window(video, start, end, roster, fps=FPS, jobs=8):
    """Timed caption lines for [start, end) of the recording, plus stats."""
    frames, seen, anchored = [], 0, 0
    with tempfile.TemporaryDirectory(prefix='captions-') as tmp:
        t0 = start
        while t0 < end:
            dur = min(CHUNK_SECONDS, end - t0)
            for old in Path(tmp).glob('*'):
                old.unlink()
            subprocess.run(
                ['ffmpeg', '-loglevel', 'error', '-ss', f'{t0:.3f}', '-t', f'{dur:.3f}',
                 '-i', str(video), '-vf', f'fps={fps}', '-q:v', '3',
                 os.path.join(tmp, 'f_%06d.jpg'), '-y'],
                check=True)
            paths = sorted(str(p) for p in Path(tmp).glob('f_*.jpg'))
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                results = list(pool.map(_ocr_frame, paths))
            for i, (lines, found) in enumerate(results):
                frames.append((t0 + i / fps, lines))
                seen += 1
                anchored += found
            t0 += dur

    timed = [(t, frame_tokens(lines, roster, keep_last=(i == len(frames) - 1)))
             for i, (t, lines) in enumerate(frames)]
    return stitch_timed(timed, roster), {'frames': seen, 'anchored': anchored}


# --- CLI -------------------------------------------------------------------

def parse_windows(s):
    out = []
    for part in (s or '').split(','):
        if part.strip():
            a, b = part.split(':')
            out.append((float(a), float(b)))
    return out


def _hms(sec):
    sec = int(sec)
    return f'{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}'


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description='Patch a far-side dropout with Meet captions.')
    ap.add_argument('video')
    ap.add_argument('transcript')
    ap.add_argument('--windows', required=True, help='start:end seconds, comma-separated')
    ap.add_argument('--parts', help='where to keep pre-caption-splice.txt and captions.txt')
    ap.add_argument('--roster',
                    default=str(Path(__file__).parent.parent / 'speakers_roster.json'))
    ap.add_argument('--caller-label', default='Caller')
    ap.add_argument('--fps', type=float, default=FPS)
    ap.add_argument('--jobs', type=int, default=8)
    args = ap.parse_args(argv)

    if not shutil.which('tesseract'):
        print('tesseract not found (brew install tesseract); captions not checked')
        return 0
    roster = load_roster(args.roster)
    lag = Config().caption_lag_seconds
    windows = parse_windows(args.windows)
    txt = Path(args.transcript)
    entries = parse_transcript(txt.read_text())

    confirmed, all_lines = [], []
    for start, end in windows:
        lines, stats = read_window(args.video, start, end, roster, args.fps, args.jobs)
        words = other_words(lines, roster.self_name)
        span = f'{_hms(start)}-{_hms(end)}'
        if stats['anchored'] == 0:
            print(f'{span}: Meet not on screen, nothing to read')
        elif words < MIN_OTHER_WORDS:
            print(f'{span}: captions show no one else talking ({words} words), '
                  'so the quiet was real, not a dropout')
        else:
            print(f'{span}: dropout confirmed, {words} caption words from others '
                  f'({stats["anchored"]}/{stats["frames"]} frames had Meet on screen)')
            confirmed.append((start, end))
            all_lines.extend(lines)

    if not confirmed:
        return 0
    patched, n = splice(entries, confirmed, all_lines, roster.self_name,
                        args.caller_label, lag)
    if args.parts:
        parts = Path(args.parts)
        parts.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(txt, parts / 'pre-caption-splice.txt')
        (parts / 'captions.txt').write_text(''.join(
            f'[{_hms(max(0, t - lag))}] [{who}] {text}\n' for t, who, text in all_lines))
    txt.write_text(render_transcript(patched))
    print(f'patched {n} caption line(s) into {txt.name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
