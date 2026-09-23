import sys

import pytest

from oracle.system.idle import idle_seconds


@pytest.mark.skipif(sys.platform != "win32", reason="GetLastInputInfo is Windows only")
def test_idle_time_is_read_from_windows():
    seconds = idle_seconds()
    assert seconds is not None and 0 <= seconds < 49.8 * 86400


def test_elsewhere_it_cannot_tell(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert idle_seconds() is None
