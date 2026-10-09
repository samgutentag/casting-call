import numpy as np
from PIL import Image


def crop_region(frame_path, region):
    """region = (x, y, w, h). Returns a PIL Image cropped to it."""
    x, y, w, h = region
    return Image.open(frame_path).crop((x, y, x + w, y + h))


def band_present(image, dark_fraction_min=0.35, dark_threshold=90):
    """Occlusion check: Meet's caption band is a large dark translucent strip.
    Returns True if enough of the crop is dark. Drops when Meet is covered or the
    tab switches to a light window.
    """
    gray = np.asarray(image.convert('L'))
    return float((gray < dark_threshold).mean()) >= dark_fraction_min


# Meet's red end-call button. It sits directly under the caption panel wherever
# the window is dragged, so it anchors the caption crop when the window moves.
HANGUP_RGB = (202, 38, 45)
HANGUP_TOLERANCE = 60      # summed |dR|+|dG|+|dB|
DOWNSCALE = 4              # search a quarter-size frame; the button stays ~25x17 px there
HANGUP_WIDTH_4K = 108      # px at 3840 wide; caption offsets are measured in these


def _components(mask):
    """Bounding boxes (x0, y0, x1, y1, n) of 4-connected True regions."""
    from collections import deque

    h, w = mask.shape
    seen = np.zeros(mask.shape, dtype=bool)
    out = []
    for y0, x0 in zip(*np.nonzero(mask)):
        if seen[y0, x0]:
            continue
        seen[y0, x0] = True
        queue = deque([(y0, x0)])
        xs, ys = [], []
        while queue:
            y, x = queue.popleft()
            ys.append(y); xs.append(x)
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        out.append((min(xs), min(ys), max(xs), max(ys), len(xs)))
    return out


def find_hangup(image):
    """(cx, cy, w, h) of Meet's end-call button in full-frame px, or None.

    A pill of exactly that red, wider than tall and mostly filled. Size is
    checked in proportion to the frame so a 1080p recording works like a 4K one.
    Red avatar tiles and recording dots fail on size. Two candidates return
    None rather than a guess, and so does a frame with Meet hidden.
    """
    img = image.convert('RGB')
    f = DOWNSCALE
    small = np.asarray(img.resize((img.width // f, img.height // f), Image.BILINEAR)).astype(int)
    mask = np.abs(small - np.array(HANGUP_RGB)).sum(axis=2) < HANGUP_TOLERANCE
    lo, hi = 0.014 * img.width / f, 0.056 * img.width / f
    found = []
    for x0, y0, x1, y1, n in _components(mask):
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        if lo <= bw <= hi and 1.2 <= bw / bh <= 2.6 and n / (bw * bh) > 0.6:
            found.append(((x0 + x1 + 1) / 2 * f, (y0 + y1 + 1) / 2 * f, bw * f, bh * f))
    return found[0] if len(found) == 1 else None


def caption_search_box(image, anchor):
    """(x0, y0, x1, y1) wide crop that holds the caption panel above an anchor.

    Deliberately generous: an open chat panel shifts the captions left of the
    toolbar, and neighboring windows can sit beside Meet. The caption column is
    picked out of this by word geometry after OCR (see backup.caption_lines),
    not by a tighter crop, because the window layout is not fixed.
    """
    cx, cy, bw, _ = anchor
    u = bw / HANGUP_WIDTH_4K
    return (max(0, int(cx - 1700 * u)), max(0, int(cy - 900 * u)),
            min(image.width, int(cx + 800 * u)), int(cy - 50 * u))
