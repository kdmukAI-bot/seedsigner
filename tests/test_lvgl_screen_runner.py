"""Unit tests for the LVGL screen runner (`seedsigner.gui.lvgl_screen_runner`) and
the `View.run_screen` dispatch seam.

These tests drive the runner directly with a faked native module. On both platforms
the native module owns the display and pumps LVGL on a background task, so the runner
only builds the screen and polls for the result — no flush callback, no renderer lock,
no Python-side pump.

The runner is the one place the LVGL JSON shape lives: `_assemble_cfg` turns flat
view-layer attrs into the native cfg, and `_serialize_button_option` shapes one
button_list item. `View.run_screen` is a thin dispatcher that forwards `**kwargs` to
the runner as `attrs` (or instantiates a PIL Screen class).
"""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("seedsigner.hardware.buttons", MagicMock())

import seedsigner.gui.lvgl_screen_runner as lvgl_screen_runner
from seedsigner.gui.lvgl_screen_runner import (
    _translate_event, _serialize_button_option, _lvgl_color,
    _assemble_cfg, QRBrightnessEvent,
)
from seedsigner.views.view import (
    RET_CODE__BACK_BUTTON, RET_CODE__POWER_BUTTON, View,
    ButtonOption, ButtonOptionWithoutTranslation,
)
# Same gettext the View layer uses; compute expected labels through it so these
# assertions don't depend on whatever locale a prior test left active globally.
from seedsigner.compat.l10n import gettext as _


# ---------------------------------------------------------------------------
# _translate_event - LVGL result tuple -> SeedSigner return code
# ---------------------------------------------------------------------------

def test_translate_event_button_selected_returns_index():
    assert _translate_event(("button_selected", 0, "Scan")) == 0
    assert _translate_event(("button_selected", 3, "Settings")) == 3


def test_translate_event_back_and_power():
    # Back/power ride the shared button_selected path carrying the RET_CODE sentinel in the
    # index slot (canonical native shape); _translate_event returns it via the fall-through.
    assert _translate_event(("button_selected", RET_CODE__BACK_BUTTON, "back")) == RET_CODE__BACK_BUTTON
    assert _translate_event(("button_selected", RET_CODE__POWER_BUTTON, "power")) == RET_CODE__POWER_BUTTON


def test_translate_event_text_entered_returns_string():
    assert _translate_event(("text_entered", -1, "my passphrase")) == "my passphrase"
    assert _translate_event(("text_entered", -1, "")) == ""


def test_translate_event_qr_brightness_wraps_value_not_index():
    # The brightness value rides in the index slot; it must NOT be returned as a bare
    # int (which the caller would read as a button index). It comes back wrapped.
    result = _translate_event(("qr_brightness", 200, ""))
    assert isinstance(result, QRBrightnessEvent)
    assert result.value == 200
    assert not isinstance(result, int)
    # Distinct from a real button selection at the same numeric value.
    assert result != _translate_event(("button_selected", 200, "x"))


def test_qr_brightness_event_value_equality():
    assert QRBrightnessEvent(31) == QRBrightnessEvent(31)
    assert QRBrightnessEvent(31) != QRBrightnessEvent(255)
    assert QRBrightnessEvent(128) != 128


# ---------------------------------------------------------------------------
# run_lvgl_screen - build once, then poll
# ---------------------------------------------------------------------------

def test_run_lvgl_screen_returns_translated_result(monkeypatch):
    fake_lv = MagicMock()
    fake_lv.poll_for_result.return_value = ("button_selected", 2, "Tools")
    monkeypatch.setattr(lvgl_screen_runner, "_lv", fake_lv)
    monkeypatch.setattr(lvgl_screen_runner, "ensure_lvgl_runtime", lambda: None)

    # attrs=None -> a cfg-less screen, called with no positional arg.
    result = lvgl_screen_runner.run_lvgl_screen("main_menu_screen")

    assert result == 2
    # The native pump task owns the display: no flush callback, no Python-side pump.
    assert not fake_lv.set_flush_callback.called
    assert not fake_lv.lvgl_pump.called
    fake_lv.main_menu_screen.assert_called_once_with()


def test_run_lvgl_screen_assembles_cfg_and_stamps_screensaver(monkeypatch):
    """run_lvgl_screen assembles the native cfg from flat attrs and stamps the per-screen
    screensaver policy; the caller's attrs dict is left untouched."""
    fake_lv = MagicMock()
    fake_lv.poll_for_result.return_value = ("text_entered", -1, "abc")
    monkeypatch.setattr(lvgl_screen_runner, "_lv", fake_lv)
    monkeypatch.setattr(lvgl_screen_runner, "ensure_lvgl_runtime", lambda: None)

    attrs = {"initial_text": "", "allow_screensaver": False}
    result = lvgl_screen_runner.run_lvgl_screen(
        "seed_add_passphrase_screen", attrs=attrs)

    assert result == "abc"
    fake_lv.seed_add_passphrase_screen.assert_called_once_with(
        {"initial_text": "", "allow_screensaver": False})
    assert attrs == {"initial_text": "", "allow_screensaver": False}  # not mutated


def test_run_lvgl_screen_builds_once_then_polls_for_result(monkeypatch):
    """The native screen fn is a pure builder: the runner builds it exactly once (with
    NO wait_timeout_ms kwarg), then polls until a result appears. The native pump task
    owns the display + screensaver, so there is no Python-side pump or save/restore."""
    fake_lv = MagicMock()
    fake_lv.poll_for_result.side_effect = [
        None,                                # first poll: nothing yet -> poll again
        ("button_selected", 3, "Settings"),  # second poll: a selection
    ]
    monkeypatch.setattr(lvgl_screen_runner, "_lv", fake_lv)
    monkeypatch.setattr(lvgl_screen_runner, "ensure_lvgl_runtime", lambda: None)
    monkeypatch.setattr(lvgl_screen_runner, "_sleep_ms", lambda *a: None)

    result = lvgl_screen_runner.run_lvgl_screen("main_menu_screen")

    assert result == 3
    fake_lv.main_menu_screen.assert_called_once_with()
    assert fake_lv.poll_for_result.call_count == 2  # polled until the result appeared
    assert not fake_lv.lvgl_pump.called             # native task pumps, not the runner
    fake_lv.save_screen.assert_not_called()
    fake_lv.restore_screen.assert_not_called()


# ---------------------------------------------------------------------------
# View.run_screen dispatch seam - class -> PIL, str -> LVGL (forwards attrs)
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


def test_run_screen_str_forwards_kwargs_as_attrs(monkeypatch):
    captured = {}

    def fake_run_lvgl(screen, *, attrs=None):
        captured.update(screen=screen, attrs=attrs)
        return 3

    monkeypatch.setattr(
        "seedsigner.gui.lvgl_screen_runner.run_lvgl_screen", fake_run_lvgl)

    view = View.__new__(View)
    result = view.run_screen("main_menu_screen", title="Home", allow_screensaver=False)

    assert result == 3
    assert captured["screen"] == "main_menu_screen"
    assert captured["attrs"] == {"title": "Home", "allow_screensaver": False}


# ---------------------------------------------------------------------------
# _serialize_button_option - bare label vs native object form
# (parity with the screens-side read_button_list_items() contract)
# ---------------------------------------------------------------------------

def test_serialize_button_option_plain_is_bare_label():
    # No per-button styling -> bare (translated) label string; unchanged contract.
    out = _serialize_button_option(ButtonOption("Scan"))
    assert out == _("Scan")
    assert isinstance(out, str)


def test_serialize_button_option_object_form_right_icon_and_label_color():
    opt = ButtonOption("Discard seed", right_icon_name="x_icon", button_label_color="red")
    assert _serialize_button_option(opt) == {
        "label": _("Discard seed"),
        "right_icon": "x_icon",
        "label_color": "#ff0000",
    }


def test_serialize_button_option_leading_icon_and_icon_color():
    opt = ButtonOption("Scan", icon_name="scan_icon", icon_color="blue")
    assert _serialize_button_option(opt) == {
        "label": _("Scan"), "icon": "scan_icon", "icon_color": "#0000ff"}


def test_serialize_button_option_without_translation_object_form():
    opt = ButtonOptionWithoutTranslation("xpub123", icon_name="x_icon")
    assert _serialize_button_option(opt) == {"label": "xpub123", "icon": "x_icon"}


def test_serialize_button_option_plain_string_passes_through():
    assert _serialize_button_option("already a label") == "already a label"


def test_lvgl_color_maps_names_and_passes_hex_through():
    assert _lvgl_color("red") == "#ff0000"
    assert _lvgl_color("blue") == "#0000ff"
    assert _lvgl_color("#30D158") == "#30D158"  # GUIConstants hex passes through
    assert _lvgl_color(None) is None


# ---------------------------------------------------------------------------
# _assemble_cfg - flat view-layer attrs -> native cfg shape (the one place it lives)
# ---------------------------------------------------------------------------

def test_assemble_cfg_nests_top_nav_icon_and_layout():
    # The SeedOptionsView shape: fingerprint top-nav icon + left-aligned bottom list.
    cfg = _assemble_cfg({
        "title": "1A2B3C4D",
        "button_data": ["A", "B"],
        "top_nav_icon_name": "fingerprint",
        "top_nav_icon_color": "#409CFF",
        "is_button_text_centered": False,
        "is_bottom_list": True,
        "selected_button": 2,
    })
    # show_back_button not passed -> omitted (native default True).
    assert cfg["top_nav"] == {
        "title": "1A2B3C4D",
        "icon": "fingerprint",
        "icon_color": "#409CFF",
    }
    assert cfg["button_list"] == ["A", "B"]
    assert cfg["is_button_text_centered"] is False
    assert cfg["is_bottom_list"] is True
    assert cfg["initial_selected_index"] == 2
    assert cfg["allow_screensaver"] is True


def test_assemble_cfg_forwards_flat_keys_and_serializes_buttons():
    cfg = _assemble_cfg({
        "title": "Settings",
        "show_power_button": True,
        "button_data": [ButtonOption("A")],
        "text": "Choose one",
        "checked_buttons": [1],
        "button_style": "checkbox",
    })
    assert cfg["text"] == "Choose one"           # NOT translated here; caller's job
    assert cfg["top_nav"]["show_power_button"] is True
    assert cfg["button_list"] == [_("A")]        # ButtonOption self-translates its label
    assert cfg["checked_buttons"] == [1]
    assert cfg["button_style"] == "checkbox"


def test_assemble_cfg_omits_none_values_and_zero_selected():
    cfg = _assemble_cfg({
        "title": "X", "is_button_text_centered": None, "selected_button": 0,
    })
    assert cfg["top_nav"] == {"title": "X"}
    assert "is_button_text_centered" not in cfg     # None -> omitted
    assert "initial_selected_index" not in cfg      # 0 -> omitted (native default)


def test_assemble_cfg_status_screen_shape():
    cfg = _assemble_cfg({
        "status_type": "dire_warning",
        "title": "Invalid Mnemonic!",
        "show_back_button": False,
        "text": "Checksum failure.",
        "button_data": [ButtonOption("OK")],
    })
    assert cfg["status_type"] == "dire_warning"
    assert cfg["top_nav"] == {"title": "Invalid Mnemonic!", "show_back_button": False}
    assert cfg["text"] == "Checksum failure."
    assert cfg["button_list"] == [_("OK")]


def test_assemble_cfg_custom_status_forwards_hero_icon_and_color():
    # StatusType.CUSTOM: the caller-supplied hero icon glyph + color ride as TOP-LEVEL
    # cfg keys (not under top_nav, which carries the back/power/title chrome), matching
    # the native large_icon_status_screen "custom" contract.
    from seedsigner.gui.constants import SeedSignerIconConstants, GUIConstants
    cfg = _assemble_cfg({
        "status_type": "custom",
        "title": "Action Required",
        "icon": SeedSignerIconConstants.MICROSD,
        "icon_color": GUIConstants.WARNING_COLOR,
        "warning_edges": True,
        "button_data": [ButtonOption("Continue")],
    })
    assert cfg["status_type"] == "custom"
    assert cfg["icon"] == SeedSignerIconConstants.MICROSD
    assert cfg["icon_color"] == GUIConstants.WARNING_COLOR
    assert cfg["warning_edges"] is True
    # The hero icon is top-level, NOT folded into top_nav.
    assert "icon" not in cfg["top_nav"]


# ---------------------------------------------------------------------------
# _make_scan_should_continue - cancel drain
# ---------------------------------------------------------------------------
# The native pump task owns the display + input read on both platforms, so the
# should_continue callable only drains the UI event queue: empty -> keep scanning,
# a button event -> cancel.

def _fake_lv_for_scan(monkeypatch, events):
    lv = MagicMock()
    lv.poll_for_result.side_effect = list(events)
    monkeypatch.setattr(lvgl_screen_runner, "_lv", lv)
    return lv


def test_scan_should_continue_does_not_pump(monkeypatch):
    """The native pump task advances LVGL; the drain never pumps from Python."""
    lv = _fake_lv_for_scan(monkeypatch, [None])

    should_continue = lvgl_screen_runner._make_scan_should_continue()

    assert should_continue() is True
    lv.lvgl_pump.assert_not_called()


def test_scan_should_continue_cancels_on_a_button_event(monkeypatch):
    _fake_lv_for_scan(monkeypatch, [("button_selected", RET_CODE__BACK_BUTTON, None)])

    assert lvgl_screen_runner._make_scan_should_continue()() is False


def test_scan_should_continue_drains_the_queue_fully_each_tick(monkeypatch):
    """A tick keeps scanning only once the queue is empty, so a stale non-button event
    can't leave an unread cancel sitting behind it."""
    lv = _fake_lv_for_scan(monkeypatch, [("text_entered", 0, "x"), None])

    assert lvgl_screen_runner._make_scan_should_continue()() is True
    assert lv.poll_for_result.call_count == 2
