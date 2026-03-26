"""
Adapter layer that bridges LVGL C module screens into SeedSigner's
View/Screen architecture.

LVGL screens render through SeedSigner's existing ST7789 SPI driver via a
flush callback. Input is handled natively by the LVGL extension (gpiochip
ioctl GPIO reads). The Renderer.lock is held for the entire lifetime of an
LVGL screen to prevent PIL-based screens from writing concurrently.
"""
from __future__ import annotations

import array
import logging

from seedsigner.gui.screens import RET_CODE__BACK_BUTTON, RET_CODE__POWER_BUTTON

logger = logging.getLogger(__name__)

# Lazy import; set by ensure_lvgl_runtime().
_lv = None


def ensure_lvgl_runtime():
    """Import and initialize the LVGL runtime (idempotent)."""
    global _lv
    if _lv is not None:
        return
    import seedsigner_lvgl as lv
    lv.lvgl_init(hor_res=240, ver_res=240)
    lv.native_input_init()
    _lv = lv
    logger.info("LVGL runtime initialized")


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

    SeedSigner return codes:
        int index (0, 1, 2, ...) for button selection
        RET_CODE__BACK_BUTTON (1000) for back
        RET_CODE__POWER_BUTTON (1001) for power
    """
    kind, index, _label = event
    if kind == "topnav_back":
        return RET_CODE__BACK_BUTTON
    if kind == "topnav_power":
        return RET_CODE__POWER_BUTTON
    return index


def run_lvgl_screen(renderer, screen_fn, *args, **kwargs):
    """Run an LVGL screen function while holding the renderer lock.

    Args:
        renderer: The SeedSigner Renderer singleton.
        screen_fn: An LVGL screen function (e.g. _lv.main_menu_screen).
        *args, **kwargs: Passed through to screen_fn.

    Returns:
        A SeedSigner-compatible return code (int or RET_CODE constant).
    """
    ensure_lvgl_runtime()

    try:
        with renderer.lock:
            _lv.set_flush_mode("python")
            _lv.set_flush_callback(_make_flush_callback(renderer.disp))
            _lv.clear_result_queue()
            # Screen functions block internally (run_lvgl_until_result_or_timeout)
            # pumping LVGL and reading GPIO input until a result is queued.
            screen_fn(*args, **kwargs)

        event = _lv.poll_for_result()
        if event is not None:
            return _translate_event(event)
    finally:
        _lv.set_flush_callback(None)


def lvgl_main_menu_screen(renderer):
    """Run the LVGL main menu screen.

    Returns:
        Button index (0-3) or RET_CODE__POWER_BUTTON.
    """
    return run_lvgl_screen(renderer, _lv.main_menu_screen)
