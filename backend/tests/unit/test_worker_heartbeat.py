import time
from pathlib import Path

from riftlab.worker.main import _touch_heartbeat


def test_touch_heartbeat_creates_parent_and_file(tmp_path: Path):
    target = tmp_path / "nested" / "heartbeat"
    assert not target.exists()

    _touch_heartbeat(target)

    assert target.exists()


def test_touch_heartbeat_updates_mtime_on_repeat_call(tmp_path: Path):
    target = tmp_path / "heartbeat"
    _touch_heartbeat(target)
    first_mtime = target.stat().st_mtime_ns

    time.sleep(0.01)
    _touch_heartbeat(target)
    second_mtime = target.stat().st_mtime_ns

    assert second_mtime > first_mtime
