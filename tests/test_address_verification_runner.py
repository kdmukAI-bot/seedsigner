"""Unit tests for the native address-verification driver
(`run_seed_address_verification_screen`) in `seedsigner.gui.lvgl_screen_runner`.

The driver runs on BOTH platforms in production; one shared poll loop with per-platform pump
mechanics. These tests fake the native module and pin `IS_MICROPYTHON` to exercise each branch
deterministically: the shared loop/cfg tests drive the MicroPython (poll-only) branch, and the
CPython (blended display) tests at the bottom cover the pump/flush mechanics.
"""
import sys
import time
from unittest.mock import MagicMock

sys.modules.setdefault("seedsigner.hardware.buttons", MagicMock())
# The CPython branch imports the Renderer singleton; stub the module so a standalone run of
# this file doesn't pull in Pi display hardware deps (the full suite's base.py does the same).
sys.modules.setdefault("seedsigner.gui.renderer", MagicMock())

import seedsigner.gui.lvgl_screen_runner as lvgl_screen_runner
from seedsigner.models.threads import ThreadsafeCounter


def _patch_common(monkeypatch, fake_lv, is_micropython=True):
    monkeypatch.setattr(lvgl_screen_runner, "_lv", fake_lv)
    monkeypatch.setattr(lvgl_screen_runner, "ensure_lvgl_runtime", lambda: None)
    monkeypatch.setattr(lvgl_screen_runner, "_screensaver_timeout_ms", 60000)
    # Pin the platform branch: the shared loop/cfg tests exercise the MicroPython
    # (poll-only) branch; the CPython blended-display mechanics have their own tests.
    monkeypatch.setattr(lvgl_screen_runner, "IS_MICROPYTHON", is_micropython)
    monkeypatch.setattr(time, "sleep_ms", lambda *a: None, raising=False)


def _run(counter, verified_index):
    return lvgl_screen_runner.run_seed_address_verification_screen(
        address="bc1qexampleaddress",
        type_network="Single Sig - Native Segwit",
        network="mainnet",
        title="Verify Address",
        skip_label="Skip 10",
        cancel_label="Cancel",
        threadsafe_counter=counter,
        verified_index=verified_index,
    )


def test_builds_cfg_and_cancel_returns_false(monkeypatch):
    fake_lv = MagicMock()
    fake_lv.poll_for_result.return_value = ("button_selected", 1, "Cancel")  # Cancel = index 1
    _patch_common(monkeypatch, fake_lv)

    result = _run(ThreadsafeCounter(), ThreadsafeCounter(initial_value=None))

    assert result is False
    cfg = fake_lv.seed_address_verification_screen.call_args[0][0]
    assert cfg["address"] == "bc1qexampleaddress"
    assert cfg["type_network"] == "Single Sig - Native Segwit"
    assert cfg["network"] == "mainnet"
    assert cfg["top_nav"]["title"] == "Verify Address"
    assert cfg["button_list"] == ["Skip 10", "Cancel"]
    assert cfg["progress_text"] == "Checking address 0"
    assert cfg["allow_screensaver"] is False
    # Screensaver suspended for the screen's duration, then restored.
    assert fake_lv.set_screensaver_timeout.call_args_list[0][0][0] == 0
    assert fake_lv.set_screensaver_timeout.call_args_list[-1][0][0] == 60000


def test_skip_10_increments_counter_then_cancel(monkeypatch):
    fake_lv = MagicMock()
    # Skip 10 (index 0), then Cancel (index 1).
    fake_lv.poll_for_result.side_effect = [
        ("button_selected", 0, "Skip 10"),
        ("button_selected", 1, "Cancel"),
    ]
    _patch_common(monkeypatch, fake_lv)

    counter = ThreadsafeCounter()
    result = _run(counter, ThreadsafeCounter(initial_value=None))

    assert result is False
    assert counter.cur_count == 10  # Skip 10 bumped the worker's index


def test_verified_index_set_returns_true(monkeypatch):
    fake_lv = MagicMock()
    fake_lv.poll_for_result.return_value = None  # no button; the worker match drives the exit
    _patch_common(monkeypatch, fake_lv)

    verified_index = ThreadsafeCounter(initial_value=None)
    verified_index.set_value(5)  # worker already found the address

    assert _run(ThreadsafeCounter(), verified_index) is True


def test_pushes_localized_progress_line(monkeypatch):
    fake_lv = MagicMock()
    # One idle tick (pushes progress), then Cancel.
    fake_lv.poll_for_result.side_effect = [None, ("button_selected", 1, "Cancel")]
    _patch_common(monkeypatch, fake_lv)

    counter = ThreadsafeCounter()
    counter.increment(42)
    _run(counter, ThreadsafeCounter(initial_value=None))

    fake_lv.seed_address_verification_set_progress.assert_called_with("Checking address 42")


# ---------------------------------------------------------------------------
# CPython / Pi Zero blended-display pump mechanics
# ---------------------------------------------------------------------------

def _patch_cpython(monkeypatch, fake_lv):
    """Drive the CPython (blended display) branch: fake the Renderer singleton the driver
    pulls in."""
    _patch_common(monkeypatch, fake_lv, is_micropython=False)
    import seedsigner.gui.renderer as renderer_mod
    fake_renderer = MagicMock()
    monkeypatch.setattr(renderer_mod, "Renderer",
                        MagicMock(get_instance=MagicMock(return_value=fake_renderer)))
    return fake_renderer


def test_cpython_installs_and_clears_flush_callback_and_pumps(monkeypatch):
    fake_lv = MagicMock()
    fake_lv.poll_for_result.return_value = ("button_selected", 1, "Cancel")  # Cancel exits
    _patch_cpython(monkeypatch, fake_lv)

    result = _run(ThreadsafeCounter(), ThreadsafeCounter(initial_value=None))

    assert result is False
    # Blended display: python flush mode + callback installed at build, dropped on exit.
    fake_lv.set_flush_mode.assert_called_once_with("python")
    assert fake_lv.set_flush_callback.call_args_list[0][0][0] is not None
    assert fake_lv.set_flush_callback.call_args_list[-1][0][0] is None
    # LVGL only advances on a host pump here (the native task does this on MicroPython).
    assert fake_lv.lvgl_pump.called


def test_cpython_stops_loading_pump_and_resets_input_timer(monkeypatch):
    fake_lv = MagicMock()
    fake_lv.poll_for_result.return_value = ("button_selected", 1, "Cancel")
    _patch_cpython(monkeypatch, fake_lv)

    stop_calls = []
    monkeypatch.setattr(lvgl_screen_runner, "stop_loading_pump", lambda: stop_calls.append(1))
    import seedsigner.hardware.buttons as buttons_mod
    fake_hw_buttons = MagicMock()
    monkeypatch.setattr(buttons_mod, "HardwareButtons", fake_hw_buttons)

    _run(ThreadsafeCounter(), ThreadsafeCounter(initial_value=None))

    # A caller may hand over with a loading spinner still animating; this driver stops it.
    assert stop_calls == [1]
    # Exit resets the PIL-side input timer so the next PIL screen doesn't insta-screensave.
    fake_hw_buttons.get_instance.return_value.update_last_input_time.assert_called_once()


def test_cpython_worker_match_returns_true(monkeypatch):
    fake_lv = MagicMock()
    fake_lv.poll_for_result.return_value = None  # no button; the worker match drives the exit
    _patch_cpython(monkeypatch, fake_lv)

    verified_index = ThreadsafeCounter(initial_value=None)
    verified_index.set_value(7)  # worker already found the address

    assert _run(ThreadsafeCounter(), verified_index) is True
