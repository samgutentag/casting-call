import subprocess
from pathlib import Path


def extract_frames(mov_path, out_dir, fps):
    """Extract frames at `fps` as frame_000001.png ... Frame N is at (N-1)/fps s."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for existing in out.glob('frame_*.png'):
        existing.unlink()
    subprocess.run(
        ['ffmpeg', '-i', str(mov_path), '-vf', f'fps={fps}',
         str(out / 'frame_%06d.png'), '-y', '-loglevel', 'error'],
        check=True,
    )
    return sorted(out.glob('frame_*.png'))


def frame_time(frame_path, fps):
    index = int(Path(frame_path).stem.split('_')[1])  # 1-based
    return (index - 1) / fps
