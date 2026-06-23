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
    LVGL never write concurrently. Nothing pumps LVGL in the background, so this
    runner pumps it itself between polls.
  * MicroPython / ESP32 — the native module owns the display and pumps LVGL on a
    separate task, so there is no Python flush callback and no Python-side pump;
    the ``not IS_MICROPYTHON`` guards skip that setup and this runner only polls.

Both platforms follow one contract: the native screen function is a pure builder
(it builds the widget tree and returns immediately) and a Python loop polls for the
result.

Screensaver: the native overlay manager owns the idle screensaver on both
platforms. A C dispatcher watches the LVGL inactivity timer and swaps to / restores
from the bouncing-logo screensaver entirely in C — nothing in Python drives it. The
global timeout is handed to the native side once at init (``set_screensaver_timeout``).
Per-screen opt-out rides in the cfg as ``allow_screensaver``: a View sets it False
for screens that must stay up (e.g. camera scanning), the shared parser defaults it
true, and the native scaffold stamps the screen object so the dispatcher skips it.
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

        # Hand the idle timeout to the native overlay manager, which owns the
        # screensaver on both platforms (it watches the LVGL inactivity timer and
        # swaps to / restores from the screensaver itself). Set once here; nothing
        # in Python drives the screensaver after this.
        lv.set_screensaver_timeout(_screensaver_timeout_ms)

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
        allow_screensaver: If True (default), the native overlay manager may run
            the idle screensaver over this screen; set False for screens that must
            stay up. Carried to the native side via ``cfg["allow_screensaver"]``.

    Returns:
        A SeedSigner-compatible return code (int index, RET_CODE constant, or a
        str for text-entry screens), or None if the screen exited without a
        result.
    """
    ensure_lvgl_runtime()
    # Resolve a screen passed by name now that _lv is guaranteed initialized.
    screen_fn = getattr(_lv, screen) if isinstance(screen, str) else screen

    # Copy any dict cfg so the caller's cfg (which may hold live ButtonOptions used
    # elsewhere) is never mutated, then stamp the per-screen policy and serialize
    # button options on the copy.
    if isinstance(cfg, dict):
        cfg = dict(cfg)
        # Per-screen screensaver policy rides in the cfg JSON: the shared parser
        # reads the key (defaulting absent to allowed) and the native scaffold
        # stamps the screen object so the overlay dispatcher skips it. Cfg-less
        # screens (e.g. main_menu) carry no dict and fall through to the default.
        cfg["allow_screensaver"] = allow_screensaver
        # Serialize any ButtonOptions in the button_list into their native (string)
        # form. Screen-agnostic: every screen that carries a button_list
        # (button_list_screen, large_icon_status_screen, ...) gets the same
        # treatment. Duck-typed via to_lvgl(); plain strings pass through.
        if cfg.get("button_list"):
            cfg["button_list"] = [b.to_lvgl() if hasattr(b, "to_lvgl") else b
                                  for b in cfg["button_list"]]

    args = (cfg,) if cfg is not None else ()

    # One contract, two mechanics. On both platforms the native screen fn is a pure
    # builder — it builds the widget tree and returns immediately — and a Python loop
    # polls for the result; the native overlay manager owns the screensaver. The
    # branches differ only in how LVGL gets pumped, not in architecture: MicroPython's
    # native task pumps LVGL and owns the display, so Python only polls; CPython still
    # shares the panel with the legacy PIL pipeline (the blended display), so this
    # runner pumps LVGL itself under renderer.lock and routes frames through the PIL
    # driver's flush callback. That blended-display coupling is the last transitional
    # difference — at the PIL-screen cutover the Pi native layer will own the display
    # and pump in the background like the ESP32 task already does, and the two branches
    # become one.
    if IS_MICROPYTHON:
        # The native LVGL loop (a separate task) processes input asynchronously and
        # queues results; poll until one appears. The native module owns display,
        # input, the pump, and its own idle screensaver, so none of the CPython
        # blended-display machinery below (flush callback, renderer.lock, Python-side
        # pump) applies here.
        import time
        _lv.clear_result_queue()
        screen_fn(*args)
        while True:
            event = _lv.poll_for_result()
            if event is not None:
                return _translate_event(event)
            time.sleep_ms(20)

    # CPython / Pi Zero blended display. Build the screen once, then pump LVGL and
    # poll in a loop, holding renderer.lock around each pump so PIL and LVGL never
    # write the panel concurrently. The native overlay dispatcher fires inside the
    # pump (lv_timer_handler), so the screensaver activates/restores on its own with
    # nothing to do here. Releasing the lock and sleeping briefly between pumps hands
    # the GIL back to background threads (the PIL-era cooperative model).
    import time
    try:
        with renderer.lock:
            # Blended display: route LVGL pixels through the PIL driver.
            _lv.set_flush_mode("python")
            _lv.set_flush_callback(_make_flush_callback(renderer.disp))
            _lv.clear_result_queue()
            screen_fn(*args)
        while True:
            with renderer.lock:
                _lv.lvgl_pump(5, 1)
                event = _lv.poll_for_result()
            if event is not None:
                # Reset the PIL-side input timer so returning to a PIL screen
                # doesn't immediately re-trigger the PIL screensaver.
                from seedsigner.hardware.buttons import HardwareButtons
                HardwareButtons.get_instance().update_last_input_time()
                return _translate_event(event)
            time.sleep(0.005)
    finally:
        _lv.set_flush_callback(None)
