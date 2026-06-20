"""
Runs LVGL C-module screens within SeedSigner's View/Screen architecture — the one
place the Python app drives the native ``seedsigner_lvgl_screens`` module.

A View runs an LVGL screen by passing its native screen-function *name* to
``View.run_screen`` (the dispatch seam), which forwards here. The screen is
identified by name — never by importing the native module into a View — so the
business logic stays free of any ``seedsigner_lvgl_screens`` dependency and the
flow-test harness (which patches ``run_screen``) never touches the native path.

Two render models, one runner:

  * CPython / Pi Zero — "blended display": LVGL renders through SeedSigner's
    existing ST7789 SPI driver via a Python flush callback, sharing the panel
    with the PIL screens. ``Renderer.lock`` is held around the render so PIL and
    LVGL never write concurrently.
  * MicroPython / ESP32 — the native module owns the display, so there is no
    Python flush callback; the ``not IS_MICROPYTHON`` guards skip that setup.

Screensaver: an LVGL screen activates the screensaver after the configured idle
timeout by default. Screens that must stay up (e.g. camera scanning) pass
``allow_screensaver=False``.
"""
import array
import logging

from seedsigner.compat import IS_MICROPYTHON
from seedsigner.compat.threading import Lock
from seedsigner.views.view import RET_CODE__BACK_BUTTON, RET_CODE__POWER_BUTTON

logger = logging.getLogger(__name__)

# Lazy handle to the native module; set by ensure_lvgl_runtime().
_lv = None

# Global screensaver timeout, read once from the Controller during init.
_screensaver_timeout_ms = 0

# Guards the one-time init. ensure_lvgl_runtime() can be called concurrently by
# the BackgroundImportThread and by the main thread (the first screen render),
# and the native init must not run twice.
_init_lock = Lock()


def ensure_lvgl_runtime():
    """Import and initialize the LVGL runtime (idempotent, thread-safe).

    Raises ImportError when the native ``seedsigner_lvgl_screens`` module is absent
    (dev/CI machines, non-LVGL builds). Callers that must degrade gracefully —
    the BackgroundImportThread warm-up — wrap this in ``try/except ImportError``.
    """
    global _lv, _screensaver_timeout_ms
    if _lv is not None:
        return
    with _init_lock:
        # Re-check under the lock: another thread may have finished initializing.
        if _lv is not None:
            return
        import seedsigner_lvgl_screens as lv
        if IS_MICROPYTHON:
            # On-device the native module sets up display + input itself.
            lv.init()
        else:
            # Pi Zero (CPython): LVGL renders through the existing ST7789 driver
            # via a flush callback; input is read natively (gpiochip ioctl).
            lv.lvgl_init(hor_res=240, ver_res=240)
            lv.native_input_init()

        from seedsigner.controller import Controller
        _screensaver_timeout_ms = Controller.get_instance().screensaver_activation_ms

        # Publish _lv last: until it is set, other threads keep waiting on the
        # lock rather than seeing a partially-initialized runtime.
        _lv = lv

    logger.info("LVGL runtime initialized (screensaver timeout=%dms)",
                _screensaver_timeout_ms)


def _make_flush_callback(display_driver):
    """Return a flush callback that writes LVGL RGB565 pixels through an existing
    SeedSigner display driver instance (CPython / blended-display only)."""
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
        ("text_entered", -1, text)            # e.g. a confirmed passphrase

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
        # String-valued result (text-entry screens): the entered text rides in
        # the label slot; hand it back to the caller as the string it is.
        return label
    return index


def run_lvgl_screen(renderer, screen, *, cfg=None, allow_screensaver=True):
    """Run an LVGL screen while holding the renderer lock; return its result.

    Args:
        renderer: The SeedSigner Renderer singleton.
        screen: The native screen-function *name* (e.g. "main_menu_screen"). A
            string is resolved against the native module after init — passing the
            name rather than ``_lv.<fn>`` avoids dereferencing ``_lv`` before the
            runtime exists. A callable is still accepted for direct use.
        cfg: Optional JSON-style config dict handed to the native screen as its
            single positional argument (screens that take no config omit it).
        allow_screensaver: If True (default), activate the screensaver after the
            global idle timeout; set False for screens that must stay up.

    Returns:
        A SeedSigner-compatible return code (int index, RET_CODE constant, or a
        str for text-entry screens), or None if the screen exited without a
        result.
    """
    ensure_lvgl_runtime()
    # Resolve a screen passed by name now that _lv is guaranteed initialized.
    screen_fn = getattr(_lv, screen) if isinstance(screen, str) else screen
    args = (cfg,) if cfg is not None else ()
    timeout_ms = _screensaver_timeout_ms if allow_screensaver else 0

    try:
        while True:
            with renderer.lock:
                if not IS_MICROPYTHON:
                    # Blended display: route LVGL pixels through the PIL driver.
                    _lv.set_flush_mode("python")
                    _lv.set_flush_callback(_make_flush_callback(renderer.disp))
                _lv.clear_result_queue()
                if timeout_ms > 0:
                    screen_fn(*args, wait_timeout_ms=timeout_ms)
                else:
                    screen_fn(*args)

            event = _lv.poll_for_result()
            if event is not None:
                # Reset the PIL-side input timer so returning to a PIL screen
                # doesn't immediately re-trigger the screensaver.
                from seedsigner.hardware.buttons import HardwareButtons
                HardwareButtons.get_instance().update_last_input_time()
                return _translate_event(event)

            # No result means the idle timeout fired — run the screensaver and
            # loop back, preserving the LVGL screen's focus/scroll state.
            if timeout_ms > 0:
                _lv.save_screen()
                try:
                    lvgl_screensaver_screen(renderer)
                finally:
                    _lv.restore_screen()
                # Debounce: let the wakeup press release so it doesn't register
                # as input on the re-entered screen.
                import time
                time.sleep(0.25)
                continue

            return None
    finally:
        if not IS_MICROPYTHON:
            _lv.set_flush_callback(None)


def lvgl_screensaver_screen(renderer):
    """Run the LVGL screensaver (bouncing logo); blocks until any input.

    Invoked both from an LVGL screen's idle timeout (above, wrapped in
    save_screen/restore_screen) and from a PIL context
    (Controller.start_screensaver). On CPython the PIL canvas is saved and
    restored so the underlying screen repaints; on MicroPython LVGL owns the
    panel, so there is no canvas to preserve.
    """
    if IS_MICROPYTHON:
        run_lvgl_screen(renderer, "screensaver_screen", allow_screensaver=False)
        return

    last_screen = renderer.canvas.copy()
    try:
        run_lvgl_screen(renderer, "screensaver_screen", allow_screensaver=False)
    finally:
        renderer.show_image(last_screen)
