"""
Adapter layer that bridges LVGL C module screens into SeedSigner's
View/Screen architecture.

LVGL screens render through SeedSigner's existing ST7789 SPI driver via a
flush callback. Input is handled natively by the LVGL extension (gpiochip
ioctl GPIO reads). The Renderer.lock is held for the entire lifetime of an
LVGL screen to prevent PIL-based screens from writing concurrently.

Screensaver: by default all LVGL screens activate the screensaver after
the configured timeout. Screens that should not trigger the screensaver
(e.g. camera scanning) pass allow_screensaver=False.
"""
from __future__ import annotations

import array
import logging
import threading

from seedsigner.gui.screens import RET_CODE__BACK_BUTTON, RET_CODE__POWER_BUTTON

logger = logging.getLogger(__name__)

# Lazy import; set by ensure_lvgl_runtime().
_lv = None

# Global screensaver timeout; set once during init from Controller setting.
_screensaver_timeout_ms = 0

# Guards the one-time init. ensure_lvgl_runtime() can be called concurrently by
# the BackgroundImportThread and by the main thread (the first screen render),
# and lvgl_init()/native_input_init() must not run twice.
_init_lock = threading.Lock()


def ensure_lvgl_runtime():
    """Import and initialize the LVGL runtime (idempotent, thread-safe)."""
    global _lv, _screensaver_timeout_ms
    if _lv is not None:
        return
    with _init_lock:
        # Re-check now that we hold the lock: another thread may have finished.
        if _lv is not None:
            return
        import seedsigner_lvgl as lv
        lv.lvgl_init(hor_res=240, ver_res=240)
        lv.native_input_init()

        from seedsigner.controller import Controller
        _screensaver_timeout_ms = Controller.get_instance().screensaver_activation_ms

        # Publish _lv last: until it's set, other threads keep waiting on the
        # lock rather than seeing a partially-initialized runtime.
        _lv = lv

    logger.info("LVGL runtime initialized (screensaver timeout=%dms)",
                _screensaver_timeout_ms)


def _make_flush_callback(display_driver):
    """Return a flush callback that writes LVGL RGB565 pixels through an
    existing SeedSigner display driver instance."""
    def _flush(x1, y1, x2, y2, buf):
        # LVGL outputs little-endian RGB565; ST7789 expects big-endian.
        arr = array.array("H", buf)
        arr.byteswap()
        display_driver.blit_rgb565(x1, y1, x2, y2, arr.tobytes())
    return _flush


def _translate_event(event):
    """Map an LVGL result event tuple to a SeedSigner return code.

    LVGL events:
        ("button_selected", index, label)
        ("topnav_back", -1, "topnav_back")
        ("topnav_power", -1, "topnav_power")
        ("text_entered", -1, text)            # e.g. confirmed passphrase

    SeedSigner return codes:
        int index (0, 1, 2, ...) for button selection
        RET_CODE__BACK_BUTTON (1000) for back
        RET_CODE__POWER_BUTTON (1001) for power
        str text for a confirmed text-entry screen
    """
    kind, index, label = event
    if kind == "topnav_back":
        return RET_CODE__BACK_BUTTON
    if kind == "topnav_power":
        return RET_CODE__POWER_BUTTON
    if kind == "text_entered":
        # String-valued result (text-entry screens). The entered text rides in
        # the label slot; hand it back to the caller as the string it is.
        return label
    return index


def run_lvgl_screen(renderer, screen_fn, *args, allow_screensaver=True, **kwargs):
    """Run an LVGL screen function while holding the renderer lock.

    Args:
        renderer: The SeedSigner Renderer singleton.
        screen_fn: The LVGL screen to run, given as the attribute *name* on the
            native module (e.g. "main_menu_screen"). Passing the name rather
            than _lv.<fn> avoids dereferencing _lv before the runtime is
            initialized — _lv is None until ensure_lvgl_runtime() runs, and the
            background import thread may not have finished init when the first
            screen renders. (A callable is still accepted for back-compat.)
        allow_screensaver: If True (default), activate the screensaver after
            the global timeout. Set False for screens that should stay active
            indefinitely (e.g. camera scanning).
        *args, **kwargs: Passed through to screen_fn.

    Returns:
        A SeedSigner-compatible return code (int or RET_CODE constant).
    """
    ensure_lvgl_runtime()
    # Resolve a screen passed by name now that _lv is guaranteed initialized.
    if isinstance(screen_fn, str):
        screen_fn = getattr(_lv, screen_fn)
    timeout_ms = _screensaver_timeout_ms if allow_screensaver else 0

    try:
        while True:
            with renderer.lock:
                _lv.set_flush_mode("python")
                _lv.set_flush_callback(_make_flush_callback(renderer.disp))
                _lv.clear_result_queue()
                if timeout_ms > 0:
                    screen_fn(*args, wait_timeout_ms=timeout_ms, **kwargs)
                else:
                    screen_fn(*args, **kwargs)

            event = _lv.poll_for_result()
            if event is not None:
                # Reset the PIL-side input timer so returning to a PIL screen
                # doesn't immediately trigger the screensaver.
                from seedsigner.hardware.buttons import HardwareButtons
                HardwareButtons.get_instance().update_last_input_time()
                return _translate_event(event)

            # No result means timeout — launch screensaver and loop back.
            # Save/restore the LVGL screen so focus and scroll state survive.
            if timeout_ms > 0:
                _lv.save_screen()
                try:
                    lvgl_screensaver_screen(renderer)
                finally:
                    _lv.restore_screen()
                # Debounce: wait for the wakeup press to be released so it
                # doesn't register as input on the re-entered screen.
                import time
                time.sleep(0.25)
                continue

            return None
    finally:
        _lv.set_flush_callback(None)


def lvgl_main_menu_screen(renderer):
    """Run the LVGL main menu screen.

    Returns:
        Button index (0-3) or RET_CODE__POWER_BUTTON.
    """
    return run_lvgl_screen(renderer, "main_menu_screen")


# Maps SeedAddPassphraseScreen's keyboard button-text constants to the LVGL
# passphrase screen's `initial_mode` values.
_PASSPHRASE_KEYBOARD_MODES = {
    "abc": "lower",
    "ABC": "upper",
    "123": "digits",
    "!@#": "symbols",
    "*[]": "symbols",
}


def lvgl_seed_add_passphrase_screen(renderer, passphrase="", title=None, initial_keyboard="abc"):
    """Run the LVGL BIP-39 passphrase entry screen.

    Args:
        passphrase: Text to pre-fill (e.g. when re-editing an existing entry).
        title: Top-nav title. Defaults to "BIP-39 Passphrase".
        initial_keyboard: A SeedAddPassphraseScreen.KEYBOARD__*_BUTTON_TEXT
            value; mapped to the C screen's initial_mode.

    Returns:
        The entered passphrase string on confirm (may be empty), or
        RET_CODE__BACK_BUTTON if the user backed out.
    """
    cfg = {
        "top_nav": {
            "title": title or "BIP-39 Passphrase",
            "show_back_button": True,
            "show_power_button": False,
        },
        "initial_text": passphrase or "",
        "initial_mode": _PASSPHRASE_KEYBOARD_MODES.get(initial_keyboard, "lower"),
    }
    # The native passphrase binding is METH_VARARGS only (no wait_timeout_ms
    # kwarg) and rebuilds the screen on each invocation, so it can't ride the
    # screensaver re-entry path without discarding in-progress text. Run it
    # without the screensaver; it blocks until the user confirms or backs out.
    return run_lvgl_screen(
        renderer, "seed_add_passphrase_screen", cfg, allow_screensaver=False
    )


def lvgl_screensaver_screen(renderer):
    """Run the LVGL screensaver (bouncing logo). Blocks until any input.

    When called from an LVGL screen timeout, the caller wraps this with
    save_screen/restore_screen to preserve LVGL state.

    When called from a PIL context (controller.start_screensaver), save
    and restore the PIL canvas to repaint the display."""
    last_screen = renderer.canvas.copy()
    try:
        run_lvgl_screen(renderer, "screensaver_screen", allow_screensaver=False)
    finally:
        renderer.show_image(last_screen)
