import math

import numpy as np
import parselmouth
import pytest

from casting_call.prosody import (
    Utterance, analyze, format_annotation, utterances_from_transcript,
)


def _tone(f0_start, f0_end, dur=1.5, sr=16000):
    """Synthesized vowel-ish tone gliding f0_start -> f0_end Hz."""
    t = np.arange(int(dur * sr)) / sr
    f0 = np.linspace(f0_start, f0_end, len(t))
    phase = 2 * np.pi * np.cumsum(f0) / sr
    # add harmonics so the pitch tracker locks on like it would for voice
    sig = 0.6 * np.sin(phase) + 0.25 * np.sin(2 * phase) + 0.1 * np.sin(3 * phase)
    return sig


def _sound_from(segments, sr=16000, gap=0.5):
    """Concatenate signal segments with silence gaps; return Sound + spans."""
    silence = np.zeros(int(gap * sr))
    parts, spans, t = [], [], 0.0
    for seg in segments:
        parts.append(silence)
        t += gap
        start = t
        parts.append(seg)
        t += len(seg) / sr
        spans.append((start, t))
    parts.append(silence)
    return parselmouth.Sound(np.concatenate(parts), sampling_frequency=sr), spans


def test_terminal_slope_sign(tmp_path):
    rising = _tone(150, 260)
    falling = _tone(260, 150)
    snd, spans = _sound_from([rising, falling])
    wav = tmp_path / 'tones.wav'
    snd.save(str(wav), 'WAV')

    utts = [Utterance(s, e, 'X', 'test words here') for s, e in spans]
    analyzed = analyze(wav, utts)

    (_, f_rise, _), (_, f_fall, _) = analyzed
    assert f_rise.f0_slope is not None and f_rise.f0_slope > 0
    assert f_fall.f0_slope is not None and f_fall.f0_slope < 0


def test_zscores_within_speaker(tmp_path):
    # three same tones + one much higher: the outlier should carry the
    # largest |z| for median pitch
    segs = [_tone(150, 155), _tone(150, 155), _tone(150, 155), _tone(320, 330)]
    snd, spans = _sound_from(segs)
    wav = tmp_path / 'tones.wav'
    snd.save(str(wav), 'WAV')

    utts = [Utterance(s, e, 'X', 'w w w') for s, e in spans]
    analyzed = analyze(wav, utts)
    zs = [z.get('f0_med', 0.0) for _, _, z in analyzed]
    assert max(zs) == zs[-1]
    assert zs[-1] > 1.0


def test_utterances_from_transcript_windows():
    entries = [
        {'t': 10, 'label': 'You', 'text': 'hello there'},
        {'t': 14, 'label': 'Caller', 'text': 'hi'},
        {'t': 100, 'label': 'You', 'text': 'long gap before this'},
    ]
    utts = utterances_from_transcript(entries, 'You', max_gap=15.0)
    assert len(utts) == 2
    assert utts[0].t_start == 10 and utts[0].t_end == 14   # capped by next line
    assert utts[1].t_end == 115                            # capped by max_gap


def test_energy_clamps_to_voiced_region(tmp_path):
    # same tone, one utterance window padded with 2s of trailing silence:
    # clamped energy should read nearly identical, not collapsed
    tone = _tone(180, 190, dur=1.5)
    snd, spans = _sound_from([tone, tone])
    wav = tmp_path / 'tones.wav'
    snd.save(str(wav), 'WAV')

    tight = Utterance(spans[0][0], spans[0][1], 'X', 'w w w')
    padded = Utterance(spans[1][0], spans[1][1] + 2.0, 'X', 'w w w')
    (_, f_tight, _), (_, f_padded, _) = analyze(wav, [tight, padded])
    assert f_tight.energy is not None and f_padded.energy is not None
    assert abs(f_tight.energy - f_padded.energy) < 3.0  # dB


def test_baseline_ignores_short_backchannels(tmp_path):
    # three long steady tones + one short extreme backchannel: the
    # backchannel must not drag the baseline, so the long tones stay near 0σ
    long_tones = [_tone(150, 155, dur=2.0), _tone(160, 165, dur=2.0),
                  _tone(170, 175, dur=2.0)]
    yip = _tone(400, 410, dur=0.7)
    snd, spans = _sound_from(long_tones + [yip])
    wav = tmp_path / 'tones.wav'
    snd.save(str(wav), 'WAV')

    utts = [Utterance(s, e, 'X', 'w w w') for s, e in spans]
    analyzed = analyze(wav, utts)
    long_zs = [abs(z.get('f0_med', 0.0)) for _, _, z in analyzed[:3]]
    yip_z = analyzed[3][2].get('f0_med', 0.0)
    # three evenly spread points sit at ~1.22σ of their own baseline by
    # construction; the excluded backchannel must score far outside that
    assert max(long_zs) < 1.5
    assert yip_z > 5.0
    assert yip_z > 4 * max(long_zs)


def test_format_annotation_thresholds():
    assert format_annotation({}) == ''
    tag = format_annotation({'f0_med': 1.4, 'energy': 0.9}, slope_hz=55)
    assert 'pitch +1.4σ' in tag and 'rising' in tag and 'energy +0.9σ' in tag
    # sub-threshold values stay silent
    assert format_annotation({'f0_med': 0.2, 'energy': 0.3}, slope_hz=5) == ''
