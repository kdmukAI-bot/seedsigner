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

    Raises ImportError when neither native module name is present (dev/CI machines,
    non-LVGL builds). Callers that must degrade gracefully — the
    BackgroundImportThread warm-up — wrap this in ``try/except ImportError``.
    """
    global _lv, _screensaver_timeout_ms
    if _lv is not None:
        return
    with _init_lock:
        # Re-check under the lock: another thread may have finished initializing.
        if _lv is not None:
            return
        try:
            import seedsigner_lvgl_screens as lv
        except ImportError:
            # Interim: the ESP32 firmware still registers the pre-rename native
            # module name. Drop this fallback once the builder renames it to
            # seedsigner_lvgl_screens (builder docs/rename-native-module.md).
            import seedsigner_lvgl as lv
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


def _pump_until_result(timeout_ms):
    """Drive the LVGL cycle on the *current* (already-built) screen until a result
    is queued or ``timeout_ms`` ms elapse (0 = run until a result).

    Built on the public ``lvgl_pump`` (which runs ``lv_timer_handler`` for a short
    slice) so that a screen restored after the screensaver resumes *in place* — its
    focus/scroll intact — instead of being rebuilt from config, which would reset
    focus to the default button. Mirrors the native run-until-result loop that the
    one-shot screen functions use internally. CPython/blended path only.
    """
    import time
    deadline = None if timeout_ms == 0 else time.time() + timeout_ms / 1000.0
    while True:
        _lv.lvgl_pump(5, 1)
        event = _lv.poll_for_result()
        if event is not None:
            return event
        if deadline is not None and time.time() >= deadline:
            return None


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

    if IS_MICROPYTHON:
        # On-device the native screen fn builds the screen and returns immediately;
        # the native LVGL loop (a separate task) processes touch asynchronously and
        # queues results. Poll until one appears — mirrors the builder's reference
        # driver. The native module owns display, input, and its own idle
        # screensaver, so none of the CPython blended-display machinery below (flush
        # callback, save/restore_screen, Python-driven screensaver) applies here.
        import time
        _lv.clear_result_queue()
        screen_fn(*args)
        while True:
            event = _lv.poll_for_result()
            if event is not None:
                return _translate_event(event)
            time.sleep_ms(20)

    timeout_ms = _screensaver_timeout_ms if allow_screensaver else 0

    # The screen is BUILT once (the native screen fn builds the widget tree and runs
    # its event loop). After an idle-timeout screensaver we RESUME the same screen by
    # pumping its event loop in place — rebuilding would reset focus to the default
    # button. save_screen/restore_screen keep that screen object alive across the
    # screensaver so the pump has a live screen to resume.
    build = True
    try:
        while True:
            with renderer.lock:
                if not IS_MICROPYTHON:
                    # Blended display: route LVGL pixels through the PIL driver.
                    _lv.set_flush_mode("python")
                    _lv.set_flush_callback(_make_flush_callback(renderer.disp))
                _lv.clear_result_queue()
                if build:
                    # First render: build the screen and run until a result/timeout.
                    if timeout_ms > 0:
                        screen_fn(*args, wait_timeout_ms=timeout_ms)
                    else:
                        screen_fn(*args)
                    event = _lv.poll_for_result()
                else:
                    # Resume the screen restored after the screensaver (focus/scroll
                    # intact) by pumping its event loop — no rebuild.
                    event = _pump_until_result(timeout_ms)

            if event is not None:
                # Reset the PIL-side input timer so returning to a PIL screen
                # doesn't immediately re-trigger the screensaver.
                from seedsigner.hardware.buttons import HardwareButtons
                HardwareButtons.get_instance().update_last_input_time()
                return _translate_event(event)

            if timeout_ms == 0:
                # No screensaver requested and no result: nothing left to wait on.
                return None

            # Idle timeout fired: run the screensaver over the saved screen, then
            # loop back to resume that same screen via the pump branch above.
            _lv.save_screen()
            try:
                lvgl_screensaver_screen(renderer)
            finally:
                _lv.restore_screen()
            # Debounce: let the wakeup press release so it doesn't register as
            # input on the resumed screen.
            import time
            time.sleep(0.25)
            build = False
    finally:
        if not IS_MICROPYTHON:
            _lv.set_flush_callback(None)


def lvgl_screensaver_screen(renderer):
    """Run the LVGL screensaver (bouncing logo); blocks until any input.

    Invoked only from an LVGL screen's idle timeout in ``run_lvgl_screen`` above,
    wrapped in ``save_screen``/``restore_screen``. Repainting the screen the
    screensaver covered is that caller's job: it restores its saved LVGL screen
    and re-renders on the next loop iteration. This function deliberately does not
    touch the PIL canvas — on the blended display the canvas holds the last *PIL*
    screen drawn (e.g. a menu visited before the LVGL screen took over via
    ``blit_rgb565``), so re-showing it here would flash that stale frame for a
    moment on wake before the LVGL screen repaints.
    """
    run_lvgl_screen(renderer, "screensaver_screen", allow_screensaver=False)
