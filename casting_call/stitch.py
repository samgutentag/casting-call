"""Stitch rolling on-screen captions into a full attributed transcript.

The speaker layer (locate/read/timeline) answers "who was talking when" so
Whisper output can be relabeled. This module goes further: when a channel's
audio is missing or unusable (see callcheck), the caption text itself is the
transcript. Meet redraws the caption box as text scrolls, so consecutive
frames overlap heavily; we OCR every frame and merge each frame's new tail
onto the committed token stream via longest-overlap alignment.

Ported from the deepgram-application caption_work experiment; speakers now
come from the roster instead of a hardcoded map.
"""
import os
import re
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

from .roster import match_name

CONF = 70          # drop OCR words below this confidence (faded mid-scroll text)
ANCHOR = 4         # minimum matching-block size to trust an alignment
TAILWIN = 400      # align frames against only the last N committed tokens

# UI text that can bleed into the caption crop (menus, tile labels, the page
# behind a transparent overlay). Matching lines are dropped entirely.
JUNK_RE = re.compile(
    r'elgato|wave link|system default|speaker device|microphonefx'
    r'|stream recording|test speakers?|\bdevice\b|\bplayin'
    r'|meet\.google|live captions|english captions|jump to bottom', re.I)

SPEAKER_MATCH_THRESHOLD = 0.72


def is_gibberish(text):
    """True for faded mid-scroll lines that slip past the confidence filter,
    e.g. 'yUU UUl PIUUULLS OU' — real captions rarely have mid-word case
    flips, long all-caps runs, or vowelless words."""
    toks = [re.sub(r'[^A-Za-z]', '', t) for t in text.split()]
    toks = [t for t in toks if t]
    if len(toks) < 3:
        return False
    weird = 0
    for t in toks:
        if re.search(r'[a-z][A-Z]', t) \
           or (t.isupper() and len(t) >= 4) \
           or (len(t) >= 4 and not re.search(r'[aeiouAEIOU]', t)):
            weird += 1
    return weird / len(toks) > 0.3


def speaker_of(line, roster):
    """Roster name if this OCR line is a caption speaker label, else None.

    Labels are short (a name, nothing else); require <= 4 tokens so ordinary
    caption text can't fuzzy-match into a name.
    """
    key = re.sub(r"[^a-z' ]", '', line.lower()).strip()
    if not key or len(key.split()) > 4:
        return None
    return match_name(key, roster, SPEAKER_MATCH_THRESHOLD)


def ocr_lines(path):
    """Ordered, cleaned caption lines for one frame (conf-filtered TSV).

    Reads a cached <frame>.tsv next to the image when present, else runs
    tesseract. Lines are rebuilt from word geometry: grouped by tesseract's
    block/paragraph/line ids, words sorted left-to-right, lines top-to-bottom.
    """
    tsv = os.path.splitext(path)[0] + '.tsv'
    if os.path.exists(tsv):
        with open(tsv) as f:
            out = f.read()
    else:
        out = subprocess.run(['tesseract', path, '-', '--psm', '6', 'tsv'],
                             capture_output=True, text=True).stdout
    rows = {}
    for ln in out.splitlines()[1:]:
        p = ln.split('\t')
        if len(p) < 12:
            continue
        try:
            conf = float(p[10]); left = int(p[6]); top = int(p[7])
        except ValueError:
            continue
        word = p[11].strip()
        if not word or conf < CONF:
            continue
        key = (p[2], p[3], p[4])
        rows.setdefault(key, []).append((top, left, word))
    line_recs = []
    for words in rows.values():
        words.sort(key=lambda w: w[1])
        tops = sorted(w[0] for w in words)
        med_top = tops[len(tops) // 2]
        text = ' '.join(w[2] for w in words).strip()
        if (len(re.sub(r'[^a-zA-Z]', '', text)) >= 2
                and not JUNK_RE.search(text) and not is_gibberish(text)):
            line_recs.append((med_top, text))
    line_recs.sort(key=lambda r: r[0])
    return [t for _, t in line_recs]


def frame_tokens(path, roster, keep_last=False):
    """Flatten a frame to a token stream with inline ('SPK', name) markers.

    The bottom caption line is Meet's interim result (still being revised),
    so we drop it and commit it only once it has scrolled up and stabilized
    in a later frame. keep_last=True commits everything (final frame).
    """
    lines = ocr_lines(path)
    if not keep_last and len(lines) > 1:
        lines = lines[:-1]
    toks = []
    for line in lines:
        sp = speaker_of(line, roster)
        if sp:
            toks.append(('SPK', sp))
        else:
            toks.extend(line.split())
    return toks


def normtok(t):
    if isinstance(t, tuple):
        return t
    return re.sub(r'[^a-z0-9]', '', t.lower())


def merge(committed, frame):
    """Append frame's new tail to committed, aligned on the matching block
    that reaches furthest into committed (robust to interim OCR revisions)."""
    if not committed:
        return frame[:]
    off = max(0, len(committed) - TAILWIN)
    tail = committed[off:]
    cn = [normtok(t) for t in tail]
    fn = [normtok(t) for t in frame]
    sm = SequenceMatcher(None, cn, fn, autojunk=False)
    best = None
    for a, b, size in sm.get_matching_blocks():
        if size >= ANCHOR and (best is None or a + size > best[0] + best[2]):
            best = (a, b, size)
    if best is None:
        return committed + frame
    a, b, size = best
    return committed + frame[b + size:]


def dedupe_stutter(tokens):
    """Collapse immediately-repeated phrases (residual interim-caption
    stutter), e.g. 'like Yeah I like Yeah I think' -> 'like Yeah I think'."""
    nt = [normtok(t) for t in tokens]
    changed = True
    while changed:
        changed = False
        for n in range(8, 0, -1):
            out, outn, i = [], [], 0
            while i < len(tokens):
                if (i + 2 * n <= len(tokens)
                        and nt[i:i + n] == nt[i + n:i + 2 * n]
                        and all(nt[i:i + n])):
                    i += n
                    changed = True
                else:
                    out.append(tokens[i]); outn.append(nt[i]); i += 1
            tokens, nt = out, outn
    return tokens


def _clean_token(t):
    if isinstance(t, tuple):
        return t
    if t == '|':
        return 'I'
    return t.replace('|', 'I') if re.fullmatch(r"\|['’].*", t) else t


def stitch_frames(frames_dir, roster, fixups=()):
    """OCR + merge every frame in frames_dir -> attributed transcript text.

    fixups: iterable of (pattern, replacement) regex pairs applied to the
    final text — proper nouns the ASR/OCR chain reliably mangles.
    Returns (text, stats_dict).
    """
    files = sorted(os.path.join(frames_dir, f)
                   for f in os.listdir(frames_dir) if f.endswith('.png'))
    committed = []
    for i, path in enumerate(files):
        toks = frame_tokens(path, roster, keep_last=(i == len(files) - 1))
        if toks:
            committed = merge(committed, toks)

    # Drop any body text before the first speaker label (pre-call UI noise).
    for idx, t in enumerate(committed):
        if isinstance(t, tuple):
            committed = committed[idx:]
            break

    committed = [_clean_token(t) for t in committed]
    committed = dedupe_stutter(committed)

    segs = []
    cur, buf = None, []

    def flush():
        if buf:
            segs.append((cur or '???', ' '.join(buf)))
            buf.clear()

    for t in committed:
        if isinstance(t, tuple):
            flush(); cur = t[1]
        else:
            buf.append(t)
    flush()

    merged = []
    for sp, txt in segs:
        if merged and merged[-1][0] == sp:
            merged[-1] = (sp, merged[-1][1] + ' ' + txt)
        else:
            merged.append((sp, txt))

    text = '\n\n'.join(f'{sp}: {txt}' for sp, txt in merged)
    for pat, repl in fixups:
        text = re.sub(pat, repl, text)
    stats = {'frames': len(files), 'tokens': len(committed),
             'turns': len(merged)}
    return text, stats


# --- CLI ---------------------------------------------------------------

def stitched_output_path(mov_path, out_dir=None):
    p = Path(mov_path)
    base = Path(out_dir) if out_dir else p.parent / 'transcripts'
    return base / f'{p.stem}-stitched.txt'


def extract_band_frames(mov_path, out_dir, region, fps):
    """ffmpeg the caption band straight to cropped PNGs (crop in the filter,
    so we never write full-resolution frames to disk)."""
    x, y, w, h = region
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for existing in out.glob('band_*.png'):
        existing.unlink()
    for existing in out.glob('band_*.tsv'):
        existing.unlink()
    subprocess.run(
        ['ffmpeg', '-loglevel', 'error', '-i', str(mov_path),
         '-vf', f'fps={fps},crop={w}:{h}:{x}:{y}',
         str(out / 'band_%06d.png'), '-y'],
        check=True,
    )
    return sorted(out.glob('band_*.png'))


def ocr_frames_parallel(frames, jobs=8):
    """Pre-generate the .tsv caches tesseract-per-frame, jobs at a time.
    stitch_frames() picks the caches up instead of shelling out serially."""
    from concurrent.futures import ThreadPoolExecutor

    def one(path):
        base = os.path.splitext(str(path))[0]
        subprocess.run(['tesseract', str(path), base, '--psm', '6', 'tsv'],
                       capture_output=True)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        list(pool.map(one, frames))


def main(argv=None):
    import argparse

    from .roster import load_roster

    parser = argparse.ArgumentParser(
        description='casting-call caption stitcher: captions become the transcript',
        epilog='User guide: docs/user-guide.html (open in a browser)',
    )
    parser.add_argument('mov', help='the recording (or a directory of pre-cropped band frames)')
    parser.add_argument('--region', help='x,y,w,h caption strip crop (full-frame px); '
                                         'required unless mov is a frames directory')
    parser.add_argument('--roster',
                        default=str(Path(__file__).parent.parent / 'speakers_roster.json'))
    parser.add_argument('--out', help='output dir (default: transcripts/ next to the recording)')
    parser.add_argument('--fps', type=float, default=1.5,
                        help='caption sampling rate (default 1.5; captions scroll slowly)')
    parser.add_argument('--jobs', type=int, default=8, help='parallel OCR workers')
    args = parser.parse_args(argv)

    src = Path(args.mov)
    if src.is_dir():
        frames_dir = src
        frames = sorted(list(src.glob('*.png')))
    else:
        if not args.region:
            print('error: --region x,y,w,h required when input is a recording',
                  file=sys.stderr)
            return 2
        region = tuple(int(v) for v in args.region.split(','))
        frames_dir = (Path(args.out) if args.out else src.parent) / 'stitch_frames'
        print(f'extracting caption band at {args.fps} fps...')
        frames = extract_band_frames(src, frames_dir, region, args.fps)
    if not frames:
        print(f'error: no frames found in {frames_dir}', file=sys.stderr)
        return 1

    print(f'OCR over {len(frames)} frames ({args.jobs} workers)...')
    ocr_frames_parallel(frames, args.jobs)

    roster = load_roster(args.roster)
    text, stats = stitch_frames(str(frames_dir), roster)

    out_path = stitched_output_path(src, args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text + '\n')
    print(f"wrote {out_path} ({stats['frames']} frames, {stats['tokens']} tokens, "
          f"{stats['turns']} turns)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
