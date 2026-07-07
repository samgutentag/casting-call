from pathlib import Path

from casting_call.affect import (
    faces_output_path, pick_channel, prosody_output_path,
)
from casting_call.stitch import stitched_output_path


def test_pick_channel_follows_recording_convention():
    assert pick_channel('You') == 'left'
    assert pick_channel('Caller') == 'right'
    assert pick_channel('Dana Park') == 'right'


def test_prosody_output_path_derivation():
    out = prosody_output_path('/calls/transcripts/standup.txt', 'Caller')
    assert out == Path('/calls/transcripts/standup-caller-prosody.txt')


def test_faces_output_path_derivation():
    assert faces_output_path('/calls/rec.mov') == Path('/calls/rec-faces.txt')
    assert faces_output_path('/calls/rec.mov', '/tmp/x') == Path('/tmp/x/rec-faces.txt')


def test_stitched_output_path_derivation():
    out = stitched_output_path('/calls/rec.mov')
    assert out == Path('/calls/transcripts/rec-stitched.txt')
    assert stitched_output_path('/calls/rec.mov', '/tmp/o') == Path('/tmp/o/rec-stitched.txt')


def test_affect_help_runs_without_heavy_deps(capsys):
    # argparse --help must not import parselmouth/mediapipe
    import casting_call.affect as affect
    try:
        affect.main(['--help'])
    except SystemExit as e:
        assert e.code == 0
    assert 'affect layer' in capsys.readouterr().out
