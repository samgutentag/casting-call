from datetime import datetime

from casting_call.obslog import find_log, safe_mode


def test_find_log_picks_the_session_that_was_running_when_recording_began(tmp_path):
    for name in ['2026-10-20 09-51-41.txt', '2026-10-20 10-00-23.txt', '2026-10-20 13-28-34.txt',
                 'not-a-log.txt']:
        (tmp_path / name).write_text('')
    got = find_log(tmp_path, datetime(2026, 10, 20, 10, 1, 12))
    assert got.name == '2026-10-20 10-00-23.txt'


def test_find_log_returns_none_when_no_session_started_before_the_recording(tmp_path):
    (tmp_path / '2026-10-20 13-28-34.txt').write_text('')
    assert find_log(tmp_path, datetime(2026, 10, 20, 10, 0)) is None


def test_safe_mode_is_read_from_either_log_line():
    assert safe_mode("10:00:24.937: Safe Mode enabled.\n")
    assert safe_mode("10:00:25.331: Skipping module 'obs-websocket', not on safe list\n")
    assert not safe_mode('10:00:25.331: [obs-websocket] [obs_module_load] Module loaded.\n')
