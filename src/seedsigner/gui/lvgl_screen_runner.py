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


class QRBrightnessEvent:
    """A native ``qr_brightness`` poll event, surfaced to the QR-display frame driver.

    The native ``qr_display_screen`` emits ``("qr_brightness", <31..255>, "")`` on the
    shared poll queue whenever the user adjusts the QR background brightness. ``_translate_event``
    wraps the value in this type so the frame driver can tell it apart from a button-selection
    index (a bare ``int``): its cue to persist ``SETTING__QR_BRIGHTNESS`` and restart the
    animated sequence (re-delivering the valuable pure first frames). No other screen emits it.
    """
    def __init__(self, value: int):
        self.value = value

    def __eq__(self, other):
        # Value-equality (like ButtonOption): keeps unit-test assertions and any
        # incidental comparisons meaningful. __hash__ left unset -> unhashable, which
        # is fine (never used as a dict key / set member).
        if other.__class__ is not self.__class__:
            return NotImplemented
        return self.value == other.value

    def __repr__(self):
        return "QRBrightnessEvent({!r})".format(self.value)


def _translate_event(event):
    """Map an LVGL result event tuple to a SeedSigner return code.

    LVGL events:
        ("button_selected", index, label)
        ("topnav_back", -1, "topnav_back")
        ("topnav_power", -1, "topnav_power")
        ("text_entered", -1, text)            # e.g. a confirmed passphrase
        ("qr_brightness", 31..255, "")        # QR-display brightness change (mid-screen)

    SeedSigner return codes:
        int index (0, 1, 2, ...) for button selection
        RET_CODE__BACK_BUTTON (1000) for back
        RET_CODE__POWER_BUTTON (1001) for power
        str text for a confirmed text-entry screen
        QRBrightnessEvent for a QR-display brightness change (not a terminal result)
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
    if kind == "qr_brightness":
        # A mid-screen brightness change (only qr_display_screen emits this). Wrap the
        # 31..255 value; without this branch it would fall through to ``return index`` and
        # be misread as a button index. The QR frame driver consumes it, not a View.
        return QRBrightnessEvent(index)
    return index


# Pillow accepts CSS color *names* (e.g. "red", "blue") wherever the PIL screens took a
# fill color; the native LVGL screens parse only 6-digit hex. Translate the names the app
# actually uses for button/icon styling. Values already in "#rrggbb" form pass through
# (every GUIConstants color is 6-digit hex).
_LVGL_NAMED_COLORS = {
    "red": "#ff0000",
    "blue": "#0000ff",
}


def _lvgl_color(color):
    """Map a PIL color name/hex to the 6-digit hex the native screens require.

    None/empty -> None (caller omits the key, native applies its default).
    """
    if not color:
        return None
    if color.startswith("#"):
        return color
    return _LVGL_NAMED_COLORS.get(color.lower(), color)


def _serialize_button_option(option):
    """Build one native ``button_list`` item from a view-layer ``ButtonOption``.

    Returns the bare (resolved) label string when the option carries no per-button
    styling (byte-identical to the original text-only contract), and the object form
    ``{"label", "icon"?, "right_icon"?, "icon_color"?, "label_color"?}`` when an icon or
    color is set. Plain strings pass through unchanged.

    The label is resolved by the option itself (``resolved_label()``, translated or not
    per ``ButtonOptionWithoutTranslation``): that is the one translation owned below a
    View's ``run()``, and it stays on the view-layer vocab object, NOT here. This function
    only shapes the native JSON (icons + color mapping), mirroring how the PIL screens read
    ``ButtonOption`` fields externally.
    """
    if not hasattr(option, "resolved_label"):
        return option  # already a bare label string
    label = option.resolved_label()
    if not (option.icon_name or option.right_icon_name or option.icon_color or option.button_label_color):
        return label
    obj = {"label": label}
    if option.icon_name:
        obj["icon"] = option.icon_name
    if option.right_icon_name:
        obj["right_icon"] = option.right_icon_name
    if option.icon_color:
        obj["icon_color"] = _lvgl_color(option.icon_color)
    if option.button_label_color:
        obj["label_color"] = _lvgl_color(option.button_label_color)
    return obj


def _assemble_cfg(attrs):
    """Assemble the native screen cfg from flat view-layer attrs; the ONE place the LVGL
    JSON shape lives.

    Nests the ``top_nav`` object, serializes ``button_data`` -> ``button_list``, maps the
    renamed keys (``selected_button`` -> ``initial_selected_index``; ``top_nav_icon_*`` ->
    ``top_nav.icon*``), color-maps icon colors, copies the remaining flat keys (``text``,
    ``status_type``, ``is_bottom_list``, ... and any screen-unique keys), and stamps the
    screensaver policy. ``None`` values are omitted (native applies its default).

    NEVER translates: callers (View ``run()`` methods and the ``run_*_screen`` variants)
    hand over already-translated strings; the only ``_()`` is ``ButtonOption``'s own, via
    ``_serialize_button_option``.
    """
    attrs = dict(attrs)
    allow_screensaver = attrs.pop("allow_screensaver", True)
    cfg = {}

    # top_nav (nested), built from whichever top-nav attrs were passed.
    top_nav = {}
    title = attrs.pop("title", None)
    if title is not None:
        top_nav["title"] = title
    show_back_button = attrs.pop("show_back_button", None)
    if show_back_button is not None:
        top_nav["show_back_button"] = show_back_button
    show_power_button = attrs.pop("show_power_button", None)
    if show_power_button is not None:
        top_nav["show_power_button"] = show_power_button
    top_nav_icon_name = attrs.pop("top_nav_icon_name", None)
    if top_nav_icon_name is not None:
        top_nav["icon"] = top_nav_icon_name
    icon_color = _lvgl_color(attrs.pop("top_nav_icon_color", None))
    if icon_color is not None:
        top_nav["icon_color"] = icon_color
    if top_nav:
        cfg["top_nav"] = top_nav

    # button_data (live ButtonOptions) -> serialized native button_list.
    button_data = attrs.pop("button_data", None)
    if button_data is not None:
        cfg["button_list"] = [_serialize_button_option(b) for b in button_data]

    # PIL-era pixel scroll has no native equivalent; the native screen restores position
    # from initial_selected_index instead.
    selected_button = attrs.pop("selected_button", None)
    if selected_button:
        cfg["initial_selected_index"] = selected_button

    # Remaining flat keys pass straight through (None == omit/native default).
    for key, value in attrs.items():
        if value is not None:
            cfg[key] = value

    cfg["allow_screensaver"] = allow_screensaver
    return cfg


def run_lvgl_screen(renderer, screen, *, attrs=None):
    """Run an LVGL screen while holding the renderer lock; return its result.

    Args:
        renderer: The SeedSigner Renderer singleton.
        screen: The native screen-function *name* (e.g. "main_menu_screen"). A
            string is resolved against the native module after init — passing the
            name rather than ``_lv.<fn>`` avoids dereferencing ``_lv`` before the
            runtime exists. A callable is still accepted for direct use.
        attrs: Optional flat dict of view-layer attributes; ``_assemble_cfg`` turns it
            into the native screen cfg (nesting, serialization, screensaver policy).
            ``None`` runs a screen that takes no config.

    Returns:
        A SeedSigner-compatible return code (int index, RET_CODE constant, or a
        str for text-entry screens), or None if the screen exited without a
        result.
    """
    ensure_lvgl_runtime()
    # Resolve a screen passed by name now that _lv is guaranteed initialized.
    screen_fn = getattr(_lv, screen) if isinstance(screen, str) else screen

    # All LVGL JSON shaping happens here, in the gui layer; Views never build cfg.
    cfg = _assemble_cfg(attrs) if attrs is not None else None
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


def _make_scan_should_continue():
    """Build the ``should_continue`` callable that ends a scan on user cancel.

    During a scan the native camera overlay's back button is the only producer
    feeding the shared UI event queue, so any event drained here (a top-nav back or
    the overlay's back button) means the user backed out. Returns False to cancel,
    mirroring the Pi Zero KEY_LEFT semantics.

    The hardware/joystick back source converges on this same signal once the native
    scan path reaches a device with physical buttons (the Pi at the PIL cutover);
    today this path is ESP32/touch-only, so only the touch queue feeds it.
    """
    def should_continue():
        # Drain the UI event queue fully each tick: empty -> keep scanning; a
        # back/button event -> cancel. This is a different queue from the decoded-QR
        # ring that run_scan drains via camera_scanner.poll_new, so the two never
        # interfere.
        while True:
            event = _lv.poll_for_result()
            if event is None:
                return True
            if event[0] in ("topnav_back", "button_selected"):
                return False
    return should_continue


def run_scan_screen(decoder, *, allow_screensaver=False):
    """Drive the native camera-scan pipeline for a ScanView (MicroPython / ESP32).

    Unlike ``run_lvgl_screen`` — which builds a widget tree and polls for a button
    result — the scan is a camera pipeline: the native ``camera_scanner`` module owns
    the live preview + overlay, and Python polls decoded QR payloads into ``decoder``
    via ``scan_consumer.run_scan``. User cancel (the overlay's touch back button, or a
    hardware back/LEFT press) is surfaced through ``should_continue``.

    Returns the ScanResult; the caller (ScanView) reads ``decoder`` and
    ``result.cancelled`` to route. Returns ``None`` if the camera failed to start
    (native bring-up error) so the caller can recover instead of crashing.
    """
    ensure_lvgl_runtime()
    import camera_scanner
    from seedsigner.hardware.scan_consumer import run_scan

    # The camera preview isn't LVGL "input activity", so the native idle screensaver
    # would otherwise fire over it. Suspend it for the scan's duration by zeroing the
    # timeout, then restore (0 disables; runtime-updatable — the overlay-manager
    # contract). NOTE: after a scan longer than the timeout the inactivity clock is
    # already past it, so the screensaver may fire on the next screen until a native
    # activity-reset binding lands — a small follow-up, not a blocker.
    if not allow_screensaver:
        _lv.set_screensaver_timeout(0)
    try:
        try:
            camera_scanner.start()
        except OSError as e:
            # Native camera bring-up can fail (e.g. resource exhaustion after
            # repeated scans — a known camera-pipeline teardown leak). Don't let it
            # crash the app: log and signal failure (None) so ScanView shows a
            # recoverable notice and returns to the menu.
            logger.error("camera_scanner.start() failed: %r", e)
            return None
        try:
            # Drop any stale UI events (e.g. the menu button-press that launched us)
            # so the back-button drain in should_continue can't read one as an
            # immediate cancel.
            _lv.clear_result_queue()
            return run_scan(
                decoder, scanner=camera_scanner,
                should_continue=_make_scan_should_continue(),
            )
        finally:
            camera_scanner.stop()
    finally:
        if not allow_screensaver:
            _lv.set_screensaver_timeout(_screensaver_timeout_ms)


def _qr_frame_bytes(part):
    """The payload bytes for ``qr_display_set_frame`` (the native screen re-encodes them
    into a QR). Animated parts are UR / Specter strings -> UTF-8; bytes pass through."""
    if isinstance(part, (bytes, bytearray)):
        return bytes(part)
    return part.encode("utf-8")


def _encoder_to_qr_cfg(encoder):
    """Map an ``EncodeQR`` encoder to ``(qr_mode, data_encoding, qr_data)`` for the native
    ``qr_display_screen``'s INITIAL frame, distinguished by the first part's payload type:

      * ``bytes`` payload (``CompactSeedQrEncoder``) -> ``byte`` / ``hex`` — the binary is
        hex-serialized into the JSON cfg and the native screen decodes it back.
      * ``str`` payload (SeedQR digits, xpub, address, signed message, UR fountain frame) ->
        ``auto`` / ``utf8``.

    ``auto`` matches Python ``qrcode``'s mode auto-detect (numeric > alphanumeric > byte), so
    the all-numeric SeedQR and the byte xpub/UR frames come out byte-identical to the PIL
    ``qrcode`` output (parity verified upstream). Note this consumes the encoder's first
    ``next_part()``; the animated frame loop below continues from there and ``encoder.restart()``
    rewinds to frame 0 when the brightness tip stows."""
    from binascii import hexlify
    part = encoder.next_part()
    if isinstance(part, (bytes, bytearray)):
        return "byte", "hex", hexlify(bytes(part)).decode()
    return "auto", "utf8", part


def run_qr_display_screen(encoder, *, allow_screensaver=False):
    """Drive the native animated QR-display pipeline for a QRDisplayScreen (MicroPython / ESP32).

    The native ``qr_display_screen`` owns rendering + the brightness UI (hardware hints / touch
    slider) + the brightness tip. Python builds the initial cfg, then — for an animated
    (UR-fountain) QR — pushes successive frames via ``qr_display_set_frame`` at ~6 fps, holding
    while ``qr_display_is_tip_active()`` (so the pure first frames stay up), restarting the
    sequence when the tip stows, and persisting + restarting on a brightness change. Returns when
    the user exits (``qr_display_done``); the caller routes on its own fixed Destination and
    ignores the value (parity with the PIL QRDisplayScreen, which also returns nothing useful).

    Mirrors ``run_scan_screen``: a dedicated MicroPython-only frame driver (the View gates it by
    ``IS_MICROPYTHON``, keeping the PIL QRDisplayScreen on CPython/Pi until the cutover)."""
    ensure_lvgl_runtime()
    import time
    from seedsigner.compat.l10n import gettext as _
    from seedsigner.models.settings import Settings, SettingsConstants
    settings = Settings.get_instance()

    qr_mode, data_encoding, first_frame = _encoder_to_qr_cfg(encoder)
    cfg = {
        "qr_data": first_frame,
        "qr_mode": qr_mode,
        "data_encoding": data_encoding,
        "initial_brightness": settings.get_value(SettingsConstants.SETTING__QR_BRIGHTNESS),
        "show_brightness_tips": (
            settings.get_value(SettingsConstants.SETTING__QR_BRIGHTNESS_TIPS)
            == SettingsConstants.OPTION__ENABLED),
        # The native screen holds no strings; hand it the two brightness labels already
        # translated (mirrors the PIL QRDisplayThread, which localizes them in the gui layer).
        "brighter_text": _("Brighter"),
        "darker_text": _("Darker"),
        "allow_screensaver": allow_screensaver,
    }
    is_animated = encoder.seq_len() > 1

    # A QR being read is not LVGL "input activity", so the idle screensaver would otherwise
    # bounce over it. Suspend it for the screen's duration (0 disables; runtime-updatable —
    # the overlay-manager contract), then restore. Same known follow-up as run_scan_screen:
    # after a display longer than the timeout the next screen may screensave immediately.
    if not allow_screensaver:
        _lv.set_screensaver_timeout(0)
    try:
        _lv.clear_result_queue()
        _lv.qr_display_screen(cfg)
        was_tip_active = False
        while True:
            event = _lv.poll_for_result()
            if event is not None:
                result = _translate_event(event)
                if isinstance(result, QRBrightnessEvent):
                    # User adjusted brightness: persist it and restart so the pure first
                    # frames replay once the tip stows.
                    settings.set_value(SettingsConstants.SETTING__QR_BRIGHTNESS, result.value)
                    encoder.restart()
                    continue
                # Any other event is the user exiting the screen.
                return result

            if is_animated:
                tip_active = _lv.qr_display_is_tip_active()
                if was_tip_active and not tip_active:
                    # Tip just stowed -> rewind so the valuable pure first frames replay.
                    encoder.restart()
                if not tip_active:
                    _lv.qr_display_set_frame(_qr_frame_bytes(encoder.next_part()))
                was_tip_active = tip_active

            time.sleep_ms(166)  # ~6 fps, matching the PIL QRDisplayThread cadence
    finally:
        if not allow_screensaver:
            _lv.set_screensaver_timeout(_screensaver_timeout_ms)


