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
