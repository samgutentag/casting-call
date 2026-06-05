from dataclasses import dataclass


@dataclass
class Config:
    fps: float = 2.0
    match_threshold: float = 0.72        # difflib ratio cutoff for roster matching
    debounce_seconds: float = 1.0        # drop speaker spans shorter than this
    grace_bridge_seconds: float = 3.0    # bridge brief no-signal gaps within one speaker
    caption_lag_seconds: float = 1.5     # captions trail audio; shift lookups back by this
    caller_label: str = 'Caller'
    self_label: str = 'You'              # how the existing transcript tags Sam's channel
    # (x, y, w, h) caption-strip crop hint, full-frame px. None = must pass --region in v1.
    caption_region: tuple | None = None
