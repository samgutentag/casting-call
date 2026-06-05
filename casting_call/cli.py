import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .config import Config
from .roster import load_roster
from .sample import extract_frames
from .locate import crop_region, band_present
from .read import ocr_strip, read_active_speaker
from .timeline import build_timeline
from .review import collect_unknowns, reconcile
from .transcript import parse_transcript, resolve_attribution, render_transcript
from .coverage import coverage_report


def observe_frames(frames, region, roster, cfg):
    observations = []
    for frame in frames:
        idx = int(frame.stem.split('_')[1])
        img = crop_region(frame, region)
        if not band_present(img):
            observations.append((idx, None, None))
            continue
        name, raw = read_active_speaker(ocr_strip(img), roster, cfg.match_threshold)
        observations.append((idx, name, raw))
    return observations


def run_review(spans, frames, region, out_dir, fps):
    unknowns = collect_unknowns(spans)
    if not unknowns:
        return reconcile(spans, {})
    print(f'\n{len(unknowns)} distinct unknown speaker(s) to review.')
    print('Enter a name for each, or "skip"/Enter to make the rest Caller.\n')
    review_dir = Path(out_dir) / 'review'
    review_dir.mkdir(parents=True, exist_ok=True)
    mapping = {}
    bail = False
    for entry in unknowns:
        rid = entry['review_id']
        span = next(s for s in spans if s.review_id == rid)
        frame = min(frames, key=lambda f: abs((int(f.stem.split('_')[1]) - 1) / fps - span.start))
        crop_region(frame, region).save(review_dir / f'{rid}.png')
        try:
            answer = input(f'{rid} (raw OCR "{entry["raw_ocr"]}", crop {rid}.png) -> ').strip()
        except EOFError:
            answer = ''
        if answer.lower() in ('skip', ''):
            bail = True
            break
        mapping[rid] = answer
    return reconcile(spans, mapping, bail=bail)


def main(argv=None):
    parser = argparse.ArgumentParser(description='casting-call speaker segmentation')
    parser.add_argument('mov')
    parser.add_argument('--roster', default=str(Path(__file__).parent.parent / 'speakers_roster.json'))
    parser.add_argument('--region', help='x,y,w,h caption strip crop (full-frame px)')
    parser.add_argument('--out', help='output dir (default: alongside the mov)')
    parser.add_argument('--transcript', help='existing transcript .txt to relabel in place')
    parser.add_argument('--callers', type=int, default=None,
                        help='number of OTHER participants. 1 forces single-caller collapse; '
                             '2+ forces timed attribution. Omit to auto-detect.')
    args = parser.parse_args(argv)

    cfg = Config()
    if args.region:
        cfg.caption_region = tuple(int(v) for v in args.region.split(','))
    if cfg.caption_region is None:
        print('error: --region x,y,w,h required in v1 (auto-locate is a follow-up)', file=sys.stderr)
        return 2

    out_dir = Path(args.out) if args.out else Path(args.mov).parent
    roster = load_roster(args.roster)

    frames = extract_frames(args.mov, Path(out_dir) / 'frames', cfg.fps)
    observations = observe_frames(frames, cfg.caption_region, roster, cfg)
    spans = build_timeline(observations, cfg.fps, cfg.debounce_seconds, cfg.grace_bridge_seconds)
    spans = run_review(spans, frames, cfg.caption_region, out_dir, cfg.fps)

    speakers_path = Path(out_dir) / 'speakers.json'
    with open(speakers_path, 'w') as f:
        json.dump([asdict(s) for s in spans], f, indent=2)
    print(f'\nwrote {speakers_path} ({len(spans)} spans)')

    if args.transcript:
        entries = parse_transcript(Path(args.transcript).read_text())
        relabeled, info = resolve_attribution(
            entries, spans, cfg.caption_lag_seconds, roster.self_name,
            cfg.caller_label, callers=args.callers)
        Path(args.transcript).write_text(render_transcript(relabeled))
        if info['warning']:
            print(f"note: {info['warning']}")
        if info['mode'] == 'collapse':
            print(f"2-person call: '{info['speaker']}' is the only other speaker "
                  f"-> all {cfg.caller_label} lines attributed to them")
        report = coverage_report(relabeled, cfg.caller_label)
        print(f"coverage: {report['attributed_lines']}/{report['caller_lines']} "
              f"{cfg.caller_label} lines attributed ({report['attributed_pct']}%)")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
