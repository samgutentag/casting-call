"""CLI front door for the affect layer: prosody from audio, smiles from video.

Prosody mode (default): split one speaker's channel out of the stereo
recording, cut it into utterances using the timed transcript, extract pitch /
energy / pace via prosody.py, and write an annotated copy of that speaker's
lines. Channel picks itself from the label: You lives on the left channel,
everyone else on the right (the ripa recording convention).

Faces mode (--faces): sample the speaker's video tile, track smile intensity
and nods via faces.py, and write a summary of the warmest windows. This is
the fallback for a call where the far-end audio never made it to disk.

Heavy deps (parselmouth, mediapipe) import lazily so --help always works.
"""
import argparse
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import Config
from .transcript import parse_transcript

FACE_MODEL_DEFAULT = Path.home() / '.cache/casting-call/face_landmarker.task'
FACE_MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/face_landmarker/'
                  'face_landmarker/float16/latest/face_landmarker.task')


def pick_channel(label, self_label='You'):
    """left = your mic, right = the call (the ripa recording convention)."""
    return 'left' if label == self_label else 'right'


def prosody_output_path(transcript_path, label):
    p = Path(transcript_path)
    return p.with_name(f'{p.stem}-{label.lower()}-prosody{p.suffix}')


def faces_output_path(recording_path, out_dir=None):
    p = Path(recording_path)
    base = Path(out_dir) if out_dir else p.parent
    return base / f'{p.stem}-faces.txt'


def split_channel(recording, side, wav_out):
    chan = 'c0' if side == 'left' else 'c1'
    subprocess.run(
        ['ffmpeg', '-loglevel', 'error', '-i', str(recording),
         '-af', f'pan=mono|c0={chan}', '-ar', '16000', '-y', str(wav_out)],
        check=True,
    )


def run_prosody(args):
    from . import prosody

    entries = parse_transcript(Path(args.transcript).read_text())
    labeled = [e for e in entries if e['label'] == args.label]
    if not labeled:
        print(f'error: no [{args.label}] lines in {args.transcript}', file=sys.stderr)
        return 1

    side = args.channel or pick_channel(args.label, Config().self_label)
    utts = prosody.utterances_from_transcript(entries, args.label)
    print(f'{len(labeled)} [{args.label}] lines -> {len(utts)} utterances '
          f'({side} channel)')

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / 'channel.wav'
        split_channel(args.recording, side, wav)
        analyzed = prosody.analyze(wav, utts)

    out_path = Path(args.out) if args.out else prosody_output_path(args.transcript, args.label)
    out_path.write_text(prosody.annotate_transcript(labeled, analyzed) + '\n')
    print(f'wrote {out_path}')

    flagged = sorted(
        ((u, f, z) for u, f, z in analyzed
         if z and (u.t_end - u.t_start) >= 2.0),
        key=lambda x: -max(abs(v) for v in x[2].values()))
    if flagged and args.top:
        print(f'\ntop {min(args.top, len(flagged))} deviations '
              '(utterances >= 2s; re-listen to these):')
        for u, f, z in flagged[:args.top]:
            m, s = divmod(int(u.t_start), 60)
            tag = prosody.format_annotation(z, f.f0_slope) or \
                ' '.join(f'{k}={v:+.1f}' for k, v in z.items())
            print(f'  [{m}:{s:02d}] {u.text[:58]!r}  {tag}')
    return 0


def run_faces(args):
    from . import faces

    model = Path(args.model)
    if not model.exists():
        print(f'error: face model missing at {model}\nfetch it once with:\n'
              f'  mkdir -p {model.parent} && curl -sL -o {model} "{FACE_MODEL_URL}"',
              file=sys.stderr)
        return 1
    try:
        tile = tuple(int(v) for v in args.tile.split(','))
        assert len(tile) == 4
    except (ValueError, AssertionError):
        print('error: --tile must be x,y,w,h in full-frame pixels', file=sys.stderr)
        return 2
    x, y, w, h = tile

    with tempfile.TemporaryDirectory() as tmp:
        frames_dir = Path(tmp) / 'tile_frames'
        frames_dir.mkdir()
        subprocess.run(
            ['ffmpeg', '-loglevel', 'error', '-i', str(args.recording),
             '-vf', f'fps={args.faces_fps},crop={w}:{h}:{x}:{y}',
             '-q:v', '4', str(frames_dir / 'f%06d.jpg'), '-y'],
            check=True,
        )
        files = sorted(frames_dir.iterdir())
        print(f'{len(files)} tile frames at {args.faces_fps} fps; tracking...')
        frame_iter = ((i / args.faces_fps, p) for i, p in enumerate(files))
        track = faces.track_faces(frame_iter, model)

    detected = sum(1 for s in track.samples if s.smile is not None)
    lines = [
        f'facial affect track: {Path(args.recording).name}',
        f'face detected in {detected}/{len(track.samples)} frames; '
        f'baseline smile {track.smile_mean:.3f} (sd {track.smile_sd:.3f})',
        '',
        'warmest 30s windows (mean raw smile vs baseline):',
    ]
    bins = {}
    for s in track.samples:
        if s.smile is not None:
            bins.setdefault(int(s.t // 30), []).append(s.smile)
    scored = sorted(((statistics.mean(v), k) for k, v in bins.items()
                     if len(v) >= 20), reverse=True)
    for mean, k in scored[:10]:
        t = k * 30
        nod = ' + nodding' if track.nodding_at(t + 15) else ''
        lines.append(f'  [{t // 60}:{t % 60:02d}-{(t + 30) // 60}:{(t + 30) % 60:02d}]'
                     f'  {mean:.3f}{nod}')

    out_path = Path(args.out) if args.out else faces_output_path(args.recording)
    out_path.write_text('\n'.join(lines) + '\n')
    print(f'wrote {out_path}')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='casting-call affect layer: how it was said',
        epilog='User guide: docs/user-guide.html (open in a browser)',
    )
    parser.add_argument('recording', help='the stereo .mov/.mp4 (or a .wav for prosody)')
    parser.add_argument('--transcript', help='timed transcript ([h:mm:ss] [Label] text); '
                                             'required for prosody mode')
    parser.add_argument('--label', default='Caller',
                        help="whose lines to analyze (default: Caller)")
    parser.add_argument('--channel', choices=['left', 'right'],
                        help='override the channel (default: left for You, right otherwise)')
    parser.add_argument('--out', help='output path (default: derived from the input)')
    parser.add_argument('--top', type=int, default=10,
                        help='print the N biggest deviations (0 to silence)')
    parser.add_argument('--faces', action='store_true',
                        help='smile/nod track from the video instead of prosody')
    parser.add_argument('--tile', help='x,y,w,h of the speaker video tile (faces mode)')
    parser.add_argument('--faces-fps', type=float, default=2.0)
    parser.add_argument('--model', default=str(FACE_MODEL_DEFAULT),
                        help='face landmarker model path (faces mode)')
    args = parser.parse_args(argv)

    if args.faces:
        if not args.tile:
            print('error: --faces requires --tile x,y,w,h', file=sys.stderr)
            return 2
        return run_faces(args)

    if not args.transcript:
        print('error: prosody mode requires --transcript (or pass --faces)', file=sys.stderr)
        return 2
    return run_prosody(args)


if __name__ == '__main__':
    raise SystemExit(main())
