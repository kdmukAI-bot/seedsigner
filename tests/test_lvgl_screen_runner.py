"""Unit tests for the LVGL screen runner (`seedsigner.gui.lvgl_screen_runner`) and
the `View.run_screen` dispatch seam.

Foundation A is inert — no View renders an LVGL screen yet — so these tests drive
the runner directly with a faked native module. They cover the CPython
(blended-display) path; the MicroPython branch (`IS_MICROPYTHON`) skips the flush
callback, the renderer lock, and the Python-side pump (the native task pumps LVGL),
and is exercised on-device.

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


def test_run_lvgl_screen_injects_allow_screensaver_into_cfg(monkeypatch):
    """The per-screen policy rides in the cfg the native screen receives (the parser
    reads it; the scaffold stamps the screen object), and the caller's dict is left
    untouched — the runner copies before stamping."""
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
    fake_lv.seed_add_passphrase_screen.assert_called_once_with(
        {"initial_text": "", "allow_screensaver": False})
    assert cfg == {"initial_text": ""}  # caller's dict not mutated


def test_run_lvgl_screen_builds_once_then_pumps_for_result(monkeypatch):
    """The native screen fn is a pure builder: the runner builds it exactly once —
    with NO wait_timeout_ms kwarg (the bug that crashed button_list_screen) — then
    pumps LVGL and polls until a result appears. The native overlay manager owns the
    screensaver, so there is no Python-side save/restore. poll_for_result feeds: no
    result yet (pump again), then a real selection."""
    fake_lv = MagicMock()
    fake_lv.poll_for_result.side_effect = [
        None,                                # first poll: nothing yet -> pump again
        ("button_selected", 3, "Settings"),  # second poll: a selection
    ]
    monkeypatch.setattr(lvgl_screen_runner, "_lv", fake_lv)
    monkeypatch.setattr(lvgl_screen_runner, "ensure_lvgl_runtime", lambda: None)
    monkeypatch.setattr("time.sleep", lambda *a: None)

    result = lvgl_screen_runner.run_lvgl_screen(MagicMock(), "main_menu_screen")

    assert result == 3
    # Built exactly once, cfg-less -> no args, hence no wait_timeout_ms kwarg.
    fake_lv.main_menu_screen.assert_called_once_with()
    assert fake_lv.lvgl_pump.called  # pumped until the result appeared
    # The native overlay manager owns the screensaver now — no Python save/restore.
    fake_lv.save_screen.assert_not_called()
    fake_lv.restore_screen.assert_not_called()


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


# ---------------------------------------------------------------------------
# ButtonOption.to_lvgl — bare label vs native object form
# (parity with the screens-side read_button_list_items() contract)
# ---------------------------------------------------------------------------

from seedsigner.views.view import (
    ButtonOption, ButtonOptionWithoutTranslation, button_list_lvgl_cfg, _lvgl_color,
)
# Same gettext the View layer uses; compute expected labels through it so these
# assertions don't depend on whatever locale a prior test left active globally.
from seedsigner.compat.l10n import gettext as _


def test_button_option_to_lvgl_plain_is_bare_label():
    # No per-button styling -> bare (translated) label string; unchanged contract,
    # so plain menus serialize byte-identically.
    out = ButtonOption("Scan").to_lvgl()
    assert out == _("Scan")
    assert isinstance(out, str)


def test_button_option_to_lvgl_object_form_right_icon_and_label_color():
    opt = ButtonOption("Discard seed", right_icon_name="", button_label_color="red")
    assert opt.to_lvgl() == {
        "label": _("Discard seed"),
        "right_icon": "",
        "label_color": "#ff0000",
    }


def test_button_option_to_lvgl_leading_icon_and_icon_color():
    opt = ButtonOption("Scan", icon_name="", icon_color="blue")
    assert opt.to_lvgl() == {"label": _("Scan"), "icon": "", "icon_color": "#0000ff"}


def test_button_option_without_translation_uses_object_form_unchanged():
    opt = ButtonOptionWithoutTranslation("xpub123", icon_name="")
    assert opt.to_lvgl() == {"label": "xpub123", "icon": ""}


def test_lvgl_color_maps_names_and_passes_hex_through():
    assert _lvgl_color("red") == "#ff0000"
    assert _lvgl_color("blue") == "#0000ff"
    assert _lvgl_color("#30D158") == "#30D158"  # GUIConstants hex passes through
    assert _lvgl_color(None) is None


# ---------------------------------------------------------------------------
# button_list_lvgl_cfg — screen-level forwarding of the new native keys
# ---------------------------------------------------------------------------

def test_button_list_lvgl_cfg_forwards_top_nav_icon_and_layout():
    # The SeedOptionsView migration shape: fingerprint top-nav icon + left-aligned
    # bottom list.
    cfg = button_list_lvgl_cfg(
        title="1A2B3C4D",
        button_data=["A", "B"],
        top_nav_icon_name="",
        top_nav_icon_color="#409CFF",
        is_button_text_centered=False,
        is_bottom_list=True,
        selected_button=2,
    )
    assert cfg["top_nav"] == {
        "title": "1A2B3C4D",
        "show_back_button": True,
        "icon": "",
        "icon_color": "#409CFF",
    }
    assert cfg["is_button_text_centered"] is False
    assert cfg["is_bottom_list"] is True
    assert cfg["initial_selected_index"] == 2


def test_button_list_lvgl_cfg_forwards_text_power_and_settings_keys():
    cfg = button_list_lvgl_cfg(
        title="Settings",
        button_data=["A"],
        text="Choose one",
        show_power_button=True,
        checked_buttons=[1],
        button_style="checkbox",
    )
    assert cfg["text"] == _("Choose one")
    assert cfg["top_nav"]["show_power_button"] is True
    assert cfg["checked_buttons"] == [1]
    assert cfg["button_style"] == "checkbox"


def test_button_list_lvgl_cfg_swallows_pil_only_kwargs():
    # Any ButtonListScreen call site can string-dispatch: PIL-only kwargs (fonts,
    # selected color, pixel scroll, title font) are accepted and ignored, not a
    # TypeError. is_button_text_centered defaults to None -> key omitted (native
    # default centered).
    cfg = button_list_lvgl_cfg(
        title="X",
        button_data=["A"],
        button_font_name="some_font",
        button_font_size=20,
        button_selected_color="#FF9F0A",
        title_font_size=18,
        scroll_y_initial_offset=40,
    )
    assert cfg["top_nav"]["title"] == "X"
    assert "button_font_name" not in cfg
    assert "is_button_text_centered" not in cfg
