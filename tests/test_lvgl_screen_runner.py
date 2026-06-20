"""Unit tests for the LVGL screen runner (`seedsigner.gui.lvgl_screen_runner`) and
the `View.run_screen` dispatch seam.

Foundation A is inert — no View renders an LVGL screen yet — so these tests drive
the runner directly with a faked native module. They cover the CPython
(blended-display) path; the MicroPython branch (`IS_MICROPYTHON`) only skips the
flush-callback setup and is exercised on-device.

No `tests/base.py` scaffolding is needed; the runner has no Controller/Renderer
dependency beyond what the test injects. The native `HardwareButtons` import (lazy,
on the result path) is stubbed so the module imports without Pi hardware.
"""
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("seedsigner.hardware.buttons", MagicMock())

import seedsigner.gui.lvgl_screen_runner as lvgl_screen_runner
from seedsigner.gui.lvgl_screen_runner import _make_flush_callback, _translate_event
from seedsigner.views.view import RET_CODE__BACK_BUTTON, RET_CODE__POWER_BUTTON, View


# ---------------------------------------------------------------------------
# _translate_event — LVGL result tuple -> SeedSigner return code
# ---------------------------------------------------------------------------

def test_translate_event_button_selected_returns_index():
    assert _translate_event(("button_selected", 0, "Scan")) == 0
    assert _translate_event(("button_selected", 3, "Settings")) == 3


def test_translate_event_back_and_power():
    assert _translate_event(("topnav_back", -1, "topnav_back")) == RET_CODE__BACK_BUTTON
    assert _translate_event(("topnav_power", -1, "topnav_power")) == RET_CODE__POWER_BUTTON


def test_translate_event_text_entered_returns_string():
    assert _translate_event(("text_entered", -1, "my passphrase")) == "my passphrase"
    assert _translate_event(("text_entered", -1, "")) == ""


# ---------------------------------------------------------------------------
# _make_flush_callback — little-endian RGB565 -> big-endian, through the driver
# ---------------------------------------------------------------------------

def test_make_flush_callback_byteswaps_and_blits():
    driver = MagicMock()
    flush = _make_flush_callback(driver)
    # Two LE RGB565 pixels: 0x1234, 0xABCD stored low-byte-first.
    flush(0, 0, 1, 0, b"\x34\x12\xcd\xab")
    driver.blit_rgb565.assert_called_once_with(0, 0, 1, 0, b"\x12\x34\xab\xcd")


# ---------------------------------------------------------------------------
# run_lvgl_screen — CPython happy path (no screensaver)
# ---------------------------------------------------------------------------

def test_run_lvgl_screen_returns_translated_result(monkeypatch):
    fake_lv = MagicMock()
    fake_lv.poll_for_result.return_value = ("button_selected", 2, "Tools")
    monkeypatch.setattr(lvgl_screen_runner,"_lv", fake_lv)
    monkeypatch.setattr(lvgl_screen_runner,"ensure_lvgl_runtime", lambda: None)

    renderer = MagicMock()
    result = lvgl_screen_runner.run_lvgl_screen(renderer, "main_menu_screen", allow_screensaver=False)

    assert result == 2
    # Blended-display path: flush callback set around the render, then cleared.
    assert fake_lv.set_flush_mode.called
    fake_lv.set_flush_callback.assert_called_with(None)  # last call clears it
    fake_lv.main_menu_screen.assert_called_once_with()


def test_run_lvgl_screen_passes_cfg_to_native_screen(monkeypatch):
    fake_lv = MagicMock()
    fake_lv.poll_for_result.return_value = ("text_entered", -1, "abc")
    monkeypatch.setattr(lvgl_screen_runner,"_lv", fake_lv)
    monkeypatch.setattr(lvgl_screen_runner,"ensure_lvgl_runtime", lambda: None)

    cfg = {"initial_text": ""}
    result = lvgl_screen_runner.run_lvgl_screen(
        renderer=MagicMock(), screen="seed_add_passphrase_screen",
        cfg=cfg, allow_screensaver=False,
    )

    assert result == "abc"
    fake_lv.seed_add_passphrase_screen.assert_called_once_with(cfg)


# ---------------------------------------------------------------------------
# View.run_screen dispatch seam — class -> PIL, str -> LVGL
# ---------------------------------------------------------------------------

class _FakePilScreen:
    """Stand-in for a PIL Screen class (a `type`, so it takes the class branch)."""
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def display(self):
        return 7


def test_run_screen_class_takes_pil_path():
    view = View.__new__(View)  # bypass _initialize (no Controller/Renderer needed)
    result = view.run_screen(_FakePilScreen, title="Home")
    assert result == 7
    assert view.screen.kwargs == {"title": "Home"}


def test_run_screen_str_takes_lvgl_path(monkeypatch):
    captured = {}

    def fake_run_lvgl(renderer, screen, *, cfg=None, allow_screensaver=True):
        captured.update(renderer=renderer, screen=screen, cfg=cfg,
                        allow_screensaver=allow_screensaver)
        return 3

    monkeypatch.setattr(
        "seedsigner.gui.lvgl_screen_runner.run_lvgl_screen", fake_run_lvgl)

    view = View.__new__(View)
    view.renderer = object()
    result = view.run_screen(
        "main_menu_screen", lvgl_cfg={"a": 1}, allow_screensaver=False)

    assert result == 3
    assert captured["screen"] == "main_menu_screen"
    assert captured["cfg"] == {"a": 1}
    assert captured["allow_screensaver"] is False
    assert captured["renderer"] is view.renderer
