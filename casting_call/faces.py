"""Facial affect track from a speaker's video tile: smiles and nods.

When the far-end audio is missing (see callcheck) the video still carries the
other half of the conversation. This samples the speaker's tile at a low fps,
runs MediaPipe FaceLandmarker per frame, and reduces 478 landmarks to the two
signals that matter on a call: smile intensity and head nodding.

Smile: MediaPipe's mouthSmileLeft/Right blendshape scores (model-calibrated,
0..1) averaged. More robust than raw mouth-corner geometry across head poses.

Nod: short-window oscillation of head pitch. A nod is a 1-3 Hz vertical
rocking; we detect direction changes of the pitch signal over a sliding
window and count windows with 3+ reversals of meaningful amplitude.

Same rule as prosody: z-score against the person's own baseline. Some people
smile at rest; the signal is deviation, not the absolute score.
"""
from dataclasses import dataclass
import math
import statistics

import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

NOD_WINDOW = 2.0          # seconds of pitch history per nod check
NOD_MIN_REVERSALS = 3     # direction changes within the window
NOD_MIN_AMPLITUDE = 1.5   # degrees of pitch swing to count a reversal


@dataclass
class FaceSample:
    t: float
    smile: float | None      # 0..1 blendshape score, None if no face
    pitch_deg: float | None  # head pitch, degrees


@dataclass
class FaceTrack:
    samples: list
    smile_mean: float = 0.0
    smile_sd: float = 0.0

    def smile_z(self, t, window=2.0):
        """Mean smile z-score in [t-window/2, t+window/2]."""
        vals = [s.smile for s in self.samples
                if s.smile is not None and abs(s.t - t) <= window / 2]
        if not vals or self.smile_sd < 1e-9:
            return None
        return (sum(vals) / len(vals) - self.smile_mean) / self.smile_sd

    def nodding_at(self, t):
        """True if head pitch shows nod-like oscillation around time t."""
        pts = [(s.t, s.pitch_deg) for s in self.samples
               if s.pitch_deg is not None and abs(s.t - t) <= NOD_WINDOW]
        if len(pts) < NOD_MIN_REVERSALS + 2:
            return False
        reversals = 0
        prev_dir = 0
        for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
            delta = p1 - p0
            if abs(delta) < NOD_MIN_AMPLITUDE:
                continue
            direction = 1 if delta > 0 else -1
            if prev_dir and direction != prev_dir:
                reversals += 1
            prev_dir = direction
        return reversals >= NOD_MIN_REVERSALS


def _pitch_from_matrix(matrix):
    """Head pitch (deg) from the 4x4 facial transformation matrix."""
    # rotation row 2: [r20 r21 r22]; pitch = atan2(-r20? ) — use standard
    # decomposition: pitch about x-axis from r21 (row 2, col 1) vs r22.
    r = matrix
    return math.degrees(math.atan2(r[2][1], r[2][2]))


def track_faces(frame_iter, model_path):
    """Run FaceLandmarker over (t_seconds, image_path) pairs -> FaceTrack."""
    options = mp_vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
        num_faces=1,
    )
    samples = []
    with mp_vision.FaceLandmarker.create_from_options(options) as landmarker:
        for t, path in frame_iter:
            image = mp.Image.create_from_file(str(path))
            result = landmarker.detect(image)
            if not result.face_blendshapes:
                samples.append(FaceSample(t, None, None))
                continue
            shapes = {s.category_name: s.score
                      for s in result.face_blendshapes[0]}
            smile = (shapes.get('mouthSmileLeft', 0.0)
                     + shapes.get('mouthSmileRight', 0.0)) / 2
            pitch = None
            if result.facial_transformation_matrixes:
                m = result.facial_transformation_matrixes[0]
                pitch = _pitch_from_matrix(m)
            samples.append(FaceSample(t, smile, pitch))

    vals = [s.smile for s in samples if s.smile is not None]
    track = FaceTrack(samples)
    if len(vals) >= 10:
        track.smile_mean = sum(vals) / len(vals)
        track.smile_sd = statistics.pstdev(vals)
    return track


def annotate(track, t):
    """Compact facial tag for a moment, e.g. '{smile +1.8σ · nodding}'."""
    bits = []
    z = track.smile_z(t)
    if z is not None and abs(z) >= 0.75:
        bits.append(f"smile {z:+.1f}σ")
    if track.nodding_at(t):
        bits.append('nodding')
    return '{' + ' · '.join(bits) + '}' if bits else ''
