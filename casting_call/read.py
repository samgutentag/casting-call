import re

import pytesseract

from .roster import match_name


def _is_name_like(line: str) -> bool:
    """A Meet name label is short with no sentence-ending punctuation or code chars."""
    words = line.split()
    if not (1 <= len(words) <= 4):
        return False
    if line.rstrip()[-1:] in '.?!,:;':
        return False
    if any(ch in line for ch in '(){}[]=<>/'):
        return False
    return any(w[:1].isupper() for w in words)


def read_active_speaker(ocr_lines, roster, threshold):
    """Scan caption lines bottom-up for the most recent speaker.

    Returns (canonical_name, raw_label):
      - (name, raw)  a name-like line matched the roster at/above threshold
      - (None, raw)  a name-like line exists but below threshold (unknown -> review)
      - (None, None) no name-like line at all (occlusion / tab switch / silence)
    """
    for line in reversed(ocr_lines):
        stripped = line.strip()
        if not stripped or not _is_name_like(stripped):
            continue
        return (match_name(stripped, roster, threshold), stripped)
    return (None, None)


_UI_NOISE = {'Summarize captions', 'Summarize Captions'}


def ocr_strip(image):
    """OCR a caption-strip PIL image into a list of non-empty text lines.

    Avatar icons in Meet's caption band bleed into the name-label row. We strip any
    leading non-uppercase characters so that 'e&, You' normalises to 'You' and
    '™, Dana Park' normalises to 'Dana Park'. Lines that have no
    uppercase character (pure noise / sentence fragments) are kept as-is so the
    name-like filter can discard them normally.

    Known Meet UI chrome strings (e.g. 'Summarize captions' button) are dropped.
    """
    text = pytesseract.image_to_string(image)
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        m = re.search(r'[A-Z]', stripped)
        cleaned = stripped[m.start():] if m else stripped
        if cleaned in _UI_NOISE:
            continue
        lines.append(cleaned)
    return lines
