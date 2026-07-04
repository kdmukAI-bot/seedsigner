"""Unit tests for the native image-entropy drive loop (`run_image_entropy_screen`) in
`seedsigner.gui.lvgl_screen_runner`.

MicroPython-only in production (the View gates it by `IS_MICROPYTHON`); driven here on CPython
with a faked `camera_entropy` module + faked native LVGL module, stubbing the MicroPython-only
`time.sleep_ms`. Verifies the documented host loop: start -> capture -> get_result -> stop.
"""
import sys
import time
from unittest.mock import MagicMock

sys.modules.setdefault("seedsigner.hardware.buttons", MagicMock())

import seedsigner.gui.lvgl_screen_runner as lvgl_screen_runner


class _FakeCameraEntropy:
    """Stand-in for the native camera_entropy module: records the call order and only yields a
    result once capture() has been called."""
    def __init__(self, start_raises=False):
        self.calls = []
        self._captured = False
        self._start_raises = start_raises
        self.labels = None

    def set_labels(self, capturing_text, accept_label):
        self.calls.append("set_labels")
        self.labels = (capturing_text, accept_label)

    def start(self, seed_hash=None):
        self.calls.append("start")
        if self._start_raises:
            raise OSError("camera bring-up failed")

    def capture(self):
        self.calls.append("capture")
        self._captured = True

    def get_result(self):
        self.calls.append("get_result")
        return (b"C" * 32, b"F" * 8, 5) if self._captured else None

    def resume(self):
        self.calls.append("resume")
        self._captured = False

    def stop(self):
        self.calls.append("stop")


def _patch(monkeypatch, fake_lv, fake_cam):
    monkeypatch.setattr(lvgl_screen_runner, "_lv", fake_lv)
    monkeypatch.setattr(lvgl_screen_runner, "ensure_lvgl_runtime", lambda: None)
    monkeypatch.setattr(lvgl_screen_runner, "_screensaver_timeout_ms", 60000)
    monkeypatch.setattr(time, "sleep_ms", lambda *a: None, raising=False)
    monkeypatch.setitem(sys.modules, "camera_entropy", fake_cam)


def test_image_entropy_capture_accept_returns_chain_and_frame(monkeypatch):
    fake_lv = MagicMock()
    # preview: nothing, then the capture button; review: the accept button.
    fake_lv.poll_for_result.side_effect = [
        None,
        ("button_selected", 0, "capture"),
        ("button_selected", 0, "accept"),
    ]
    fake_cam = _FakeCameraEntropy()
    _patch(monkeypatch, fake_lv, fake_cam)

    result = lvgl_screen_runner.run_image_entropy_screen()

    assert result == (b"C" * 32, b"F" * 8)
    # Overlay strings must be handed over (localized) before the camera starts, or the native
    # Accept button + "Capturing..." text render blank.
    assert fake_cam.calls[0] == "set_labels"
    assert fake_cam.calls[1] == "start"
    assert fake_cam.labels == ("Capturing image...", "Accept")
    # Documented host loop order (set_labels -> start -> capture -> get_result -> stop).
    assert "capture" in fake_cam.calls
    assert fake_cam.calls[-1] == "stop"        # always stopped in finally
    assert fake_cam.calls.index("capture") < fake_cam.calls.index("get_result")
    # Screensaver suspended for the capture, then restored.
    assert fake_lv.set_screensaver_timeout.call_args_list[0][0][0] == 0
    assert fake_lv.set_screensaver_timeout.call_args_list[-1][0][0] == 60000


def test_image_entropy_cancel_during_preview_returns_none(monkeypatch):
    fake_lv = MagicMock()
    fake_lv.poll_for_result.side_effect = [("topnav_back", -1, "back")]
    fake_cam = _FakeCameraEntropy()
    _patch(monkeypatch, fake_lv, fake_cam)

    result = lvgl_screen_runner.run_image_entropy_screen()

    assert result is None
    assert "capture" not in fake_cam.calls   # never captured
    assert fake_cam.calls[-1] == "stop"      # still cleaned up


def test_image_entropy_reshoot_then_accept(monkeypatch):
    fake_lv = MagicMock()
    # capture, then reshoot (back in review -> resume), then capture again + accept.
    fake_lv.poll_for_result.side_effect = [
        ("button_selected", 0, "capture"),
        ("topnav_back", -1, "reshoot"),
        ("button_selected", 0, "capture"),
        ("button_selected", 0, "accept"),
    ]
    fake_cam = _FakeCameraEntropy()
    _patch(monkeypatch, fake_lv, fake_cam)

    result = lvgl_screen_runner.run_image_entropy_screen()

    assert result == (b"C" * 32, b"F" * 8)
    assert "resume" in fake_cam.calls        # reshot at least once
    assert fake_cam.calls.count("capture") == 2


def test_image_entropy_camera_start_failure_returns_none(monkeypatch):
    fake_lv = MagicMock()
    fake_cam = _FakeCameraEntropy(start_raises=True)
    _patch(monkeypatch, fake_lv, fake_cam)

    result = lvgl_screen_runner.run_image_entropy_screen()

    assert result is None
    # Screensaver restored even though start() failed.
    assert fake_lv.set_screensaver_timeout.call_args_list[-1][0][0] == 60000
