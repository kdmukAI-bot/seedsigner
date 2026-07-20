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
import gc
import logging

from seedsigner.compat import IS_MICROPYTHON
from seedsigner.compat.threading import Lock
from seedsigner.compat.time import sleep_ms as _sleep_ms
from seedsigner.models.threads import BaseThread
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

# Handle to the CPython loading-screen pump thread (None when idle).
# TRANSITIONAL — delete at the Pi native display-pump cutover (see run_loading_screen).
_loading_pump = None


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

        # Register the available language packs (the render layer bakes only the
        # English floor and keeps no compiled-in locale table, so registering each
        # pack's manifest is what makes any non-English locale renderable) and load
        # the active locale's font pack so the very first screen renders in the right
        # script. Direct native calls (not the public wrappers, which would re-enter
        # this init).
        _load_active_locale_fonts(lv)

        # Hand the camera-rotation setting to the native camera engines, which read it at
        # start(). Sticky, so this one call covers whatever the settings hold — the
        # built-in default or a value restored from settings.json — and set_value()
        # re-pushes it whenever the user changes it. Doing it here rather than at Settings
        # init is what makes the ordering safe: the native module is guaranteed present
        # inside this init, whereas Settings comes up long before it. Pi-only; the ESP32
        # camera engines do not read the setting (its settings entry routes to
        # SettingsEntryDisabledView there). Direct native call, per the note above.
        if not IS_MICROPYTHON:
            from seedsigner.models.settings import Settings, SettingsConstants
            lv.set_camera_rotation(int(Settings.get_instance().get_value(
                SettingsConstants.SETTING__CAMERA_ROTATION)))

        # The camera modules are top-level modules on the ESP but submodules of the
        # native extension on the Pi. Alias them so the bare `import camera_scanner` /
        # `import camera_entropy` in the drive loops is one shape on both platforms.
        # setdefault, not assignment: a real top-level module (the ESP case) or a test
        # double already registered must win — this papers over a platform difference,
        # it never shadows a genuine module. The getattr guard lets a no-camera
        # diagnostic build fall through to the drive loops' existing ImportError path
        # instead of raising AttributeError in here.
        import sys
        for _camera_module_name in ("camera_scanner", "camera_entropy"):
            _camera_module = getattr(lv, _camera_module_name, None)
            if _camera_module is not None:
                sys.modules.setdefault(_camera_module_name, _camera_module)

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


class QRDensityEvent:
    """A native ``qr_density`` poll event, surfaced to the QR-display frame driver.

    The native ``qr_display_screen`` emits ``("qr_density", <3..6>, "")`` on the shared poll
    queue when the user adjusts the animated-QR density slider (only when the screen was built
    with ``density_control``). ``_translate_event`` wraps the px/module value so the frame driver
    can tell it apart from a button-selection index (a bare ``int``): its cue to re-split the
    fountain at the new fragment size and persist ``SETTING__QR_DENSITY``.
    """
    def __init__(self, value: int):
        self.value = value

    def __eq__(self, other):
        if other.__class__ is not self.__class__:
            return NotImplemented
        return self.value == other.value

    def __repr__(self):
        return "QRDensityEvent({!r})".format(self.value)


def _translate_event(event):
    """Map an LVGL result event tuple to a SeedSigner return code.

    LVGL events:
        ("button_selected", index, label)     # index: a 0-based button position, OR a
                                               #   reserved sentinel (back=1000, power=1001,
                                               #   screensaver_dismiss=1100, splash=1101)
        ("text_entered", -1, text)            # e.g. a confirmed passphrase
        ("qr_brightness", 31..255, "")        # QR-display brightness change (mid-screen)
        ("qr_density", 3..6, "")              # QR-display density change (mid-screen)

    SeedSigner return codes:
        int index (0, 1, 2, ...) for button selection
        RET_CODE__BACK_BUTTON (1000) for back, RET_CODE__POWER_BUTTON (1001) for power —
            both ride the shared button_selected path with the sentinel in the index slot
            (the native sentinels equal these RET_CODE values), so they fall through as index.
        str text for a confirmed text-entry screen
        QRBrightnessEvent for a QR-display brightness change (not a terminal result)
        QRDensityEvent for a QR-display density change (not a terminal result)
    """
    kind, index, label = event
    if kind == "text_entered":
        # String-valued result (text-entry screens): the entered text rides in
        # the label slot; hand it back to the caller as the string it is.
        return label
    if kind == "qr_brightness":
        # A mid-screen brightness change (only qr_display_screen emits this). Wrap the
        # 31..255 value; without this branch it would fall through to ``return index`` and
        # be misread as a button index. The QR frame driver consumes it, not a View.
        return QRBrightnessEvent(index)
    if kind == "qr_density":
        # A mid-screen density change (only a density_control qr_display_screen emits this).
        # Wrap the 3..6 px/module value so the frame driver re-splits the fountain, not a View.
        return QRDensityEvent(index)
    return index


# The reusable cfg builders + the PIL-color -> hex mapping live in lvgl_config (MP-safe,
# one definition each). Imported here so this module (and its existing importers) still
# expose _lvgl_color, and _assemble_cfg can build the top_nav sub-object.
from seedsigner.gui.lvgl_config import _lvgl_color, top_nav as _build_top_nav


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


# The locale-pack root the native locale APIs (discover_locale_packs /
# list_available_locales / set_locale / the picker's endonym-image fetch) read from —
# the SAME self-contained packs that carry each locale's LC_MESSAGES/messages.mo. The
# font seam (here) and the .mo seam (settings bindtextdomain + get_detected_languages)
# MUST read the same place, so this IS SettingsConstants.get_catalog_root() (the single
# source of truth): "lang-packs" relative to CWD on the Pi, "/sd" (microSD root) on
# ESP32. A per-platform constant, so snapshotting it once at import is exact.
from seedsigner.models.settings_definition import SettingsConstants as _SettingsConstants
LOCALE_PACK_DIR = _SettingsConstants.get_catalog_root()


# The baked "Western floor" the locale picker can render as LIVE text: ASCII +
# Latin-1 + Latin Extended-A + General Punctuation (matches the opensans_western font
# baked into seedsigner-lvgl-screens). A native language name whose glyphs all fall
# inside these ranges renders live; anything outside needs a pre-rendered endonym
# image — every non-Latin script AND Vietnamese, whose ế/ệ live in Latin Extended
# Additional (U+1E00-1EFF), which is NOT baked.
_BAKED_FLOOR_RANGES = (
    (0x0000, 0x007F),   # Basic Latin (ASCII)
    (0x0080, 0x00FF),   # Latin-1 Supplement
    (0x0100, 0x017F),   # Latin Extended-A
    (0x2000, 0x206F),   # General Punctuation
)


def endonym_needs_image(native_name):
    """True if `native_name` has any glyph outside the baked Western floor.

    The picker's live-text-vs-endonym-image rule: fully-covered Latin names (Español,
    Čeština, Türkçe) render as live text; everything else (CJK, Cyrillic, Greek,
    Arabic, Devanagari, ... and Vietnamese) is drawn from a pre-rendered image so the
    picker never has to keep every script's font resident at once.
    """
    for ch in native_name:
        cp = ord(ch)
        covered = False
        for lo, hi in _BAKED_FLOOR_RANGES:
            if lo <= cp <= hi:
                covered = True
                break
        if not covered:
            return True
    return False


def _serialize_locale_row(row):
    """Shape one view-layer locale ``{code, english, native}`` into a native picker
    row ``{locale, english, native, image?}``.

    ``image: True`` (the screen derives the pre-rendered ``endonym_<height>.bin``) is
    stamped for natives outside the baked floor; omitted for live-text natives.
    """
    entry = {
        "locale": row["code"],
        "english": row["english"],
        "native": row["native"],
    }
    if endonym_needs_image(row["native"]):
        entry["image"] = True
    return entry


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

    # top_nav (nested), built from whichever top-nav attrs were passed (the builder omits
    # unset sub-keys and color-maps the icon color).
    top_nav = _build_top_nav(
        title=attrs.pop("title", None),
        show_back_button=attrs.pop("show_back_button", None),
        show_power_button=attrs.pop("show_power_button", None),
        icon=attrs.pop("top_nav_icon_name", None),
        icon_color=attrs.pop("top_nav_icon_color", None),
    )
    if top_nav:
        cfg["top_nav"] = top_nav

    # button_data (live ButtonOptions) -> serialized native button_list.
    button_data = attrs.pop("button_data", None)
    if button_data is not None:
        cfg["button_list"] = [_serialize_button_option(b) for b in button_data]

    # locale_picker rows: view-layer {code, english, native} -> native
    # {locale, english, native, image?}. The endonym-image decision and the pack dir
    # are resolved here so the LVGL cfg shape stays in this one place.
    rows = attrs.pop("rows", None)
    if rows is not None:
        cfg["rows"] = [_serialize_locale_row(r) for r in rows]
        cfg["font_dir"] = LOCALE_PACK_DIR

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


# --- Language selection: locale-pack discovery + font switching ----------------
# All three degrade gracefully when the native runtime is absent (dev/CI machines,
# non-LVGL builds): the app still runs on the baked Western floor / English.

def discover_locale_packs(font_dir=LOCALE_PACK_DIR):
    """(Re)scan `font_dir` and register each pack's ``manifest.json`` with the render
    layer (``ss_register_pack_manifest``).

    The render layer bakes only the English floor and keeps NO compiled-in locale table,
    so this registration is what makes every non-English locale renderable — it must run
    before locale activation (``set_locale``). Re-run on SD (re)insert to rescan.
    Returns the count registered, or 0 when the native runtime is absent."""
    try:
        ensure_lvgl_runtime()
    except ImportError:
        return 0
    try:
        return _lv.discover_locale_packs(font_dir)
    except Exception:
        return 0


def list_available_locales(font_dir=LOCALE_PACK_DIR):
    """List the locale packs present under `font_dir` — each a dict
    ``{code, endonym, image, has_image}`` — for assembling the picker. Empty list when
    the native runtime is absent or no packs are present."""
    try:
        ensure_lvgl_runtime()
    except ImportError:
        return []
    try:
        return _lv.list_available_locales(font_dir)
    except Exception:
        return []


def set_locale_fonts(locale, font_dir=LOCALE_PACK_DIR):
    """Load `locale`'s LVGL font pack (glyphs + shaping) so screens render in its
    script. Returns True on success, False if a pack is missing or the native runtime
    is absent — either way the app keeps running on the baked Western floor."""
    try:
        ensure_lvgl_runtime()
    except ImportError:
        return False
    try:
        return bool(_lv.set_locale(locale, font_dir))
    except Exception:
        # A missing/garbled pack must never block a language change.
        return False


def set_camera_rotation(degrees):
    """Push the camera-rotation setting to the native camera engines, which read it at
    start(). Sticky: it persists until changed, so callers set it on change rather than
    per scan. Pi-only — the ESP32 engines do not read it. Returns True if it reached the
    native layer.

    Deliberately does NOT call ensure_lvgl_runtime(): Settings.set_value() calls this, and
    at boot that runs while settings.json is being read — long before the runtime should
    come up. Forcing init from there would pull the whole LVGL bring-up (display, input,
    Controller) into Settings construction, re-entrantly. There is nothing to do in that
    window anyway: ensure_lvgl_runtime() pushes the settled value itself at init."""
    if _lv is None:
        return False
    _lv.set_camera_rotation(int(degrees))
    return True


def _load_active_locale_fonts(lv):
    """One-time, init-time (post-microSD-mount) locale bring-up: pack discovery, the
    active locale's font pack, AND a reload of its gettext ``.mo`` catalog.

    Called from ensure_lvgl_runtime() with the freshly imported `lv` — and on ESP32 that
    native import is what mounts the microSD, where both the packs and the ``.mo``
    catalogs live. Settings init loaded the catalog EARLIER (before the card was
    mounted), so ``compat.l10n`` fail-softed to the English passthrough; re-applying the
    locale here — now that the card is available — is what makes a persisted-at-boot
    locale actually render translated TEXT, not just switch fonts. Uses the native APIs
    directly (not the public wrappers above, which would re-enter this init); guarded
    end-to-end so any failure leaves the baked English floor in place and never blocks
    bring-up. English (no pack / no catalog) is a graceful no-op.
    """
    try:
        lv.discover_locale_packs(LOCALE_PACK_DIR)
    except Exception:
        pass
    try:
        from seedsigner.models.settings import Settings
        from seedsigner.models.settings_definition import SettingsConstants
        settings = Settings.get_instance()
        locale = settings.get_value(SettingsConstants.SETTING__LOCALE)
    except Exception:
        return
    if not locale:
        return
    try:
        lv.set_locale(locale, LOCALE_PACK_DIR)   # fonts (native)
    except Exception:
        pass
    try:
        # Text: reload the .mo now that /sd (ESP32) is mounted. On CPython this is an
        # idempotent re-set of the LANGUAGE env; on MicroPython it is the actual catalog
        # (re)load that the pre-mount Settings init could not do.
        settings.load_locale()
    except Exception:
        pass


# --- Toast overlay ------------------------------------------------------------
# A toast is NOT a screen: it's a transient banner the native overlay_manager composites
# on the display's top layer, over whatever screen is live. So it has no run_screen/cfg/
# return path — it's a fire-and-forget push (safe to call from a producer thread, e.g. the
# Pi's SD-card detector). Both functions degrade to a no-op when the native runtime is
# absent (dev/CI/host tests) so a notification never crashes or blocks its caller.

def show_toast(label_text, icon_name=None, outline_color=None, font_color=None, duration_ms=3000):
    """Show a native LVGL toast overlay, REPLACING any currently-showing toast.

    Fire-and-forget: the native overlay_manager owns everything after this call —
    auto-dismiss after ``duration_ms`` (0 = stay until dismissed/replaced), dismissal on
    any input, one-at-a-time replacement, AND screensaver coexistence (a new toast breaks
    the screensaver; screensaver activation is suppressed while the toast shows). The
    library is policy-free; the host supplies the resolved policy: ``icon_name`` is a
    ``SeedSignerIconConstants`` glyph (its values ARE the icon-font PUA glyphs, like button
    icons; None = text-only), and ``outline_color``/``font_color`` are PIL color strings.

    Assembles the flat args into the native cfg dict and hands it over — the SAME
    dict-cfg shape the screen builders use, so the binding parses it uniformly. Colors
    cross the boundary as ``0xRRGGBB`` ints (a direct ``uint32_t`` for the native
    ``toast_overlay_spec_t``); ``None`` fields are omitted so the native default applies.
    No-op when the native runtime is absent (dev/CI/host tests); a notification must never
    crash or block its caller. The binding is platform-symmetric — see the toast binding
    contract (``docs/toast-binding-contract.md`` in both seedsigner-raspi-lvgl and
    seedsigner-micropython-builder).
    """
    try:
        ensure_lvgl_runtime()
    except ImportError:
        return
    try:
        cfg = {"label_text": label_text or "", "duration_ms": int(duration_ms)}
        if icon_name:
            cfg["icon"] = icon_name
        outline = _lvgl_color(outline_color)
        if outline:
            cfg["outline_color"] = int(outline.lstrip("#"), 16)
        font = _lvgl_color(font_color)
        if font:
            cfg["font_color"] = int(font.lstrip("#"), 16)
        _lv.show_toast(cfg)
    except Exception:
        # A notification toast must never take down the caller.
        logger.exception("show_toast failed")


def dismiss_toast():
    """Dismiss the currently-showing native toast, if any (no-op when none is showing or
    the native runtime is absent).

    **LVGL-thread only** — it wraps the native ``toast_overlay_dismiss()``, which mutates
    the widget tree directly (no producer-thread marshalling; there is no thread-safe
    ``overlay_manager_dismiss_toast()``). Routine toasts self-dismiss on their
    ``duration_ms`` timer, so the app does NOT reach for this from its producer threads;
    it is the documented entry point for a future LVGL-thread caller (e.g. a screen that
    clears its own toast). See the toast binding contract (``docs/toast-binding-contract.md``
    in seedsigner-raspi-lvgl / -micropython-builder).
    """
    try:
        ensure_lvgl_runtime()
    except ImportError:
        return
    try:
        _lv.dismiss_toast()
    except Exception:
        logger.exception("dismiss_toast failed")


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
        _lv.clear_result_queue()
        screen_fn(*args)
        while True:
            event = _lv.poll_for_result()
            if event is not None:
                return _translate_event(event)
            _sleep_ms(20)

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


class _LoadingPumpThread(BaseThread):
    """CPython-only: pump LVGL under renderer.lock so the self-animating loading spinner
    keeps advancing while the main thread blocks in a long task.

    Deliberately does NOT poll_for_result — a loading screen has no terminal event, so
    the pump can never steal the next screen's result off the queue. TRANSITIONAL:
    deleted at the Pi native display-pump cutover (see run_loading_screen)."""
    def __init__(self, renderer):
        super().__init__()
        self.renderer = renderer

    def run(self):
        import time
        while self.keep_running:
            with self.renderer.lock:
                _lv.lvgl_pump(5, 1)
            time.sleep(0.02)


def run_loading_screen(text=None):
    """Show the native self-animating loading spinner (fire-and-forget).

    Unlike ``run_lvgl_screen``, the loading screen produces no terminal event and is NOT
    polled: it is a pure builder that returns immediately. Dismiss it simply by loading
    the next screen — ``View.run_screen`` calls ``stop_loading_pump`` at its dispatch seam
    and the next build tears the spinner down (its ``LV_EVENT_DELETE`` frees the timer).
    There is no ``stop()`` at the call site.

      * MicroPython/ESP32: the native display task pumps LVGL, so the spinner animates on
        its own while the VM thread blocks. Nothing else to do.
      * CPython/Pi Zero (blended display): LVGL only advances on host ``lvgl_pump``, so we
        paint one frame and start a background pump thread to keep it animating.
        TRANSITIONAL — at the Pi native display-pump cutover the native layer pumps in the
        background like the ESP32 task already does; then this whole CPython branch, the
        ``_LoadingPumpThread``, and ``stop_loading_pump`` (plus its ``run_screen`` seam
        call) are deleted and this collapses to the bare ``_lv.loading_spinner_screen(cfg)`` build.

    Degrades to a no-op when the native runtime is absent (dev/CI, ``ImportError``) or when
    the deployed firmware/.so predates the ``loading_spinner_screen`` binding, so the app change is
    safe against whatever binary is currently on-device.
    """
    global _loading_pump
    stop_loading_pump()  # never stack two spinners / two pumps
    try:
        ensure_lvgl_runtime()
    except ImportError:
        return  # native module absent (dev/CI)
    if not hasattr(_lv, "loading_spinner_screen"):
        return  # deployed firmware/.so predates the binding
    cfg = {"text": text} if text else None
    if IS_MICROPYTHON:
        _lv.clear_result_queue()
        _lv.loading_spinner_screen(cfg)
        return

    # CPython blended display: build + paint one frame, then keep it animating via the pump
    # thread. Mirrors run_lvgl_screen's flush setup; the flush callback stays installed for
    # the pump's lifetime and is dropped by stop_loading_pump.
    from seedsigner.gui.renderer import Renderer
    renderer = Renderer.get_instance()
    with renderer.lock:
        _lv.set_flush_mode("python")
        _lv.set_flush_callback(_make_flush_callback(renderer.disp))
        _lv.clear_result_queue()
        _lv.loading_spinner_screen(cfg)
        _lv.lvgl_pump(5, 1)  # paint the first frame before we return
    _loading_pump = _LoadingPumpThread(renderer)
    _loading_pump.start()


def stop_loading_pump():
    """Stop the CPython loading-screen pump thread (if any) and drop its flush callback so
    the following screen takes the panel cleanly. No-op on MicroPython / when idle.

    Called at the ``View.run_screen`` dispatch seam — the one choke point every successor
    screen (PIL or LVGL) passes through. TRANSITIONAL: deleted at the Pi native
    display-pump cutover (see run_loading_screen)."""
    global _loading_pump
    if _loading_pump is None:
        return
    import time
    pump, _loading_pump = _loading_pump, None
    pump.stop()
    # Bounded wait for the pump to finish its current iteration (no join(); matches the
    # compat.threading idiom) so it can't flush a stale frame over the next screen.
    for _ in range(20):
        if not pump.is_alive():
            break
        time.sleep(0.005)
    if _lv is not None:
        _lv.set_flush_callback(None)


def clear_screen():
    """Blank the display to black — the app's parting frame on exit.

    Loads an all-black LVGL screen and pumps it to the panel via the native clear_screen
    binding (raspi py_clear_screen -> lvgl_clear_to_black). A no-op when the native runtime
    is absent (dev/CI, ImportError) or the deployed .so/firmware predates the clear_screen
    binding, so it is safe against whatever binary is on-device.

      * CPython/Pi Zero (blended display): the native pump only reaches the ST7789 through
        the PIL driver's flush callback, so install it under renderer.lock the same way
        run_lvgl_screen does; clear_screen pumps the black frame out before returning, then
        drop the callback. TRANSITIONAL — collapses to a bare _lv.clear_screen() at the Pi
        native display-pump cutover.
      * MicroPython/ESP32: the native display task owns the panel, so a direct call is all
        that's needed. The ESP32 firmware does not bind clear_screen yet, so the hasattr
        guard keeps this a no-op there for now (matching the prior no-blank-on-exit behavior).
    """
    # A parting loading spinner would otherwise keep its pump thread flushing over our
    # black frame; stop it first (idempotent, no-op when idle) so the panel is ours.
    stop_loading_pump()
    try:
        ensure_lvgl_runtime()
    except ImportError:
        return  # native module absent (dev/CI)
    if not hasattr(_lv, "clear_screen"):
        return  # deployed .so/firmware predates the binding
    if IS_MICROPYTHON:
        _lv.clear_screen()
        return

    # CPython blended display: route the native black frame through the PIL driver.
    from seedsigner.gui.renderer import Renderer
    renderer = Renderer.get_instance()
    with renderer.lock:
        _lv.set_flush_mode("python")
        _lv.set_flush_callback(_make_flush_callback(renderer.disp))
        _lv.clear_screen()  # loads black + pumps it out to the panel
        _lv.set_flush_callback(None)


def _make_scan_should_continue(renderer=None):
    """Build the ``should_continue`` callable that ends a scan on user cancel.

    During a scan the native camera overlay's back button is the only producer
    feeding the shared UI event queue, so any event drained here (the overlay's back
    button, surfaced as a button_selected carrying the RET_CODE__BACK_BUTTON sentinel)
    means the user backed out. Returns False to cancel, mirroring the Pi Zero KEY_LEFT
    semantics.

    The hardware/joystick back source converges on this same signal once the native
    scan path reaches a device with physical buttons (the Pi at the PIL cutover);
    today this path is ESP32/touch-only, so only the touch queue feeds it.

    ``renderer`` is the CPython blended-display renderer, or None on MicroPython. When
    supplied, each tick also pumps LVGL under its lock — see should_continue below.
    """
    def drain():
        # Drain the UI event queue fully each tick: empty -> keep scanning; a
        # back/button event -> cancel. This is a different queue from the decoded-QR
        # ring that run_scan drains via camera_scanner.poll_new, so the two never
        # interfere.
        while True:
            event = _lv.poll_for_result()
            if event is None:
                return True
            if event[0] == "button_selected":
                return False

    if renderer is None:
        # MicroPython: the firmware's display task renders and reads input on its own,
        # so Python must not pump LVGL here — it would drive LVGL from two threads.
        return drain

    def should_continue():
        # The Pi has no native display task (RASPI-5), so LVGL only advances when we pump
        # it. Three things ride on this, which is why skipping it froze the whole scan
        # rather than just staling the screen: the render/flush, camera_engine_pump_consume()
        # (the hook that moves captured frames into the preview sink, so without it the
        # preview stays blank however well the camera runs), and LVGL's input read, so the
        # back button never registers. Pump before draining, so events this tick produces
        # are seen by the drain rather than a tick late.
        with renderer.lock:
            _lv.lvgl_pump(5, 1)
            return drain()
    return should_continue


def run_camera_scan(decoder):
    """Drive the camera-scan pipeline for a QR scan on either hardware target.

    ``camera_scanner`` is a platform-specific C module that both targets build to one
    shared interface: a CPython extension on the Pi Zero (from seedsigner-raspi-lvgl)
    and a MicroPython C module on the ESP32 (from seedsigner-micropython-builder).
    Because the interface is identical, this single function drives the scan on both
    platforms with no per-platform branch.

    Unlike ``run_lvgl_screen`` — which builds a widget tree and polls for a single
    button result — a scan is a continuous pipeline that loops until a QR decodes or
    the user backs out. Each frame flows through:

      1. Capture, preview, decode: ``camera_scanner`` grabs the next camera frame,
         paints it as the live preview beneath the scan overlay's LVGL UI (status/
         instructions text and a back button), and decodes any QR in C. Python is
         then handed the decoded payload bytes, never the image.
      2. Ingest: ``scan_consumer.run_scan`` drains the scanner's decoded-payload ring
         and hands each payload to a Python ``DecodeQR``. Fountain-coded BC-UR is
         reassembled natively by the cUR module (``uUR``) on both targets, so
         Python receives one complete UR payload rather than the individual animated
         parts; BBQR is the exception, its segments still arriving per-frame for
         DecodeQR to reassemble in Python.
      3. Cancel check: ``should_continue`` is polled each frame so a user cancel — the
         overlay's back button or a hardware back/LEFT press — stops the loop promptly.

    Returns the ScanResult; the caller reads ``decoder`` (the decoded payload) and
    ``result.cancelled`` to route. Returns ``None`` if the camera failed to start
    (C-module bring-up error) so the caller can recover instead of crashing.
    """
    ensure_lvgl_runtime()
    import camera_scanner
    from seedsigner.hardware.scan_consumer import run_scan

    # The camera preview isn't LVGL "input activity", so the native idle screensaver
    # would otherwise fire over it. Suspend it for the scan's duration by zeroing the
    # timeout, then restore (0 disables; runtime-updatable — the overlay-manager
    # contract). A long scan leaves LVGL's inactivity clock stale (already past the
    # timeout), but the native camera overlay resets it on teardown
    # (reset_idle_clock_on_teardown), so the successor screen still gets a full
    # screensaver window.
    #
    # TODO: this suppression should move into the native camera overlay screen, which
    # should own "no screensaver while scanning" by carrying SS_OBJ_FLAG_NO_SCREENSAVER
    # (the overlay-manager's per-screen opt-out, as an allow_screensaver=false screen
    # does). lvgl-screens to-do: stamp that flag on the camera preview overlay (and the
    # camera_entropy twin). Once the native screen owns it, drop this override and the
    # outer try/finally that exists only to restore the timeout.
    # On the Pi, LVGL pixels reach the panel only through the PIL driver's flush callback,
    # and LVGL only advances when Python pumps it (both done in should_continue's tick).
    # Install the callback for the scan's duration exactly as every other CPython flow
    # does — each drops it again on the way out, so by the time we get here there is none
    # installed and an unpumped/unflushed scan renders nothing. MicroPython's firmware
    # display task owns rendering, so it neither installs a callback nor pumps.
    renderer = None
    if not IS_MICROPYTHON:
        from seedsigner.gui.renderer import Renderer
        renderer = Renderer.get_instance()

    _lv.set_screensaver_timeout(0)
    try:
        if renderer is not None:
            with renderer.lock:
                _lv.set_flush_mode("python")
                _lv.set_flush_callback(_make_flush_callback(renderer.disp))
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
                should_continue=_make_scan_should_continue(renderer),
            )
        finally:
            camera_scanner.stop()
    finally:
        if renderer is not None:
            _lv.set_flush_callback(None)
        _lv.set_screensaver_timeout(_screensaver_timeout_ms)


def _numpy_rgb_to_rgb565(frame, rotation):
    """Convert one picamera numpy frame to a 240x240 LVGL-native RGB565 byte string.

    ``frame`` is HxWx3 uint8 RGB straight from ``Camera.read_video_stream()`` (the same
    array fed to ``DecodeQR.add_image`` — decode gets the full-res original; this only
    touches a downscaled copy for the *preview*). Pipeline mirrors the PIL preview's
    geometry: stride-2 nearest downscale 480->240 (square, so fill == plain 2x), then a
    ``90 + camera_rotation`` degree CCW rotation (``Image.rotate`` parity via ``np.rot90``).

    Output is little-endian RGB565, w*h*2 bytes, NEVER pre-swapped for the panel — the
    Stage-1 contract locked on-hardware. The active flush driver (python flush ->
    ST7789.py, native flush -> display_st7789.cpp) owns panel byte-order/BGR, so feeding
    LVGL-native keeps this flush-mode-agnostic (survives the display-driver cutover)."""
    import numpy as np

    # Stride-2 nearest downscale (480x480 -> 240x240). Both are square, so the PIL
    # path's resize_image_to_fill is a plain 2x decimation with no aspect crop.
    small = frame[::2, ::2]
    # 90 + camera_rotation degrees CCW; camera_rotation is always a multiple of 90.
    k = ((90 + int(rotation)) // 90) % 4
    if k:
        small = np.rot90(small, k)
    r = (small[:, :, 0].astype(np.uint16) & 0xF8) << 8   # RRRRR000 -> bits 15..11
    g = (small[:, :, 1].astype(np.uint16) & 0xFC) << 3   # GGGGGG00 -> bits 10..5
    b = (small[:, :, 2].astype(np.uint16) >> 3)          # BBBBB    -> bits 4..0
    rgb565 = r | g | b
    # ascontiguousarray: np.rot90 returns a non-contiguous view; tobytes() copies in C
    # order regardless, but be explicit. Native (little-endian on the ARM Pi) byte order
    # is exactly the LVGL-native RGB565 the sink expects.
    return np.ascontiguousarray(rgb565).tobytes()


class _CameraScanDecodeThread(BaseThread):
    """Camera-scan decode worker: runs ``DecodeQR.add_image()`` on a dedicated thread.

    Decode is the heavy, variable-latency step — the zbar scan runs ~100-200 ms and jitters with
    frame content. Running it on its own thread lets the preview loop push frames and pump LVGL
    at a steady, camera-rate cadence independent of decode time, and keeps every native-LVGL call
    on a single thread: this worker touches only ``decoder`` (pyzbar + the UR assembler), never
    LVGL. pyzbar reaches libzbar through ctypes, which releases the GIL for the scan, so on the
    single-core Pi the preview loop keeps running while a decode is in flight.

    Cross-thread state is plain scalars (single reads/writes are GIL-atomic). The main thread
    only READS ``percent`` / ``status`` / ``done`` / ``complete``; this worker is the sole caller
    of ``decoder`` methods, so ``DecodeQR`` is never accessed concurrently. The main thread reads
    the decoder object itself only after this worker has stopped (the runner's ``finally`` waits
    for it)."""
    def __init__(self, camera, decoder):
        super().__init__()
        self.camera = camera
        self.decoder = decoder
        self.status = None        # last DecodeQRStatus -> drives the overlay dot
        self.percent = 0          # monotonic-clamped scan progress -> drives the bar
        self.complete = False     # a COMPLETE (not INVALID) decode was reached
        self.done = False         # terminal (COMPLETE or INVALID) -> main loop exits
        self._max_pct = 0

    def run(self):
        import time
        from seedsigner.models.decode_qr import DecodeQRStatus
        while self.keep_running:
            frame = self.camera.read_video_stream()
            if frame is None:
                time.sleep(0.005)
                continue
            status = self.decoder.add_image(frame)
            # Monotonic progress: the weighted estimate can momentarily dip, so never lower it.
            try:
                pct = self.decoder.get_percent_complete(weight_mixed_frames=True)
            except Exception:
                pct = self._max_pct
            if pct < self._max_pct:
                pct = self._max_pct
            else:
                self._max_pct = pct
            self.percent = pct
            self.status = status
            if status in (DecodeQRStatus.COMPLETE, DecodeQRStatus.INVALID):
                # Either terminal state ends the scan; the caller routes COMPLETE vs INVALID
                # off the decoder object.
                self.complete = status == DecodeQRStatus.COMPLETE
                self.done = True
                return


def run_camera_preview_scan(decoder, *, instructions_text=None, allow_screensaver=False):
    """Drive the Pi Zero LVGL camera-preview scan for a ScanView.

    The Pi captures frames with picamera and decodes them with ``DecodeQR``; the live preview +
    QR-scan overlay render through the native ``camera_preview_screen`` pixel sink. Two threads:
    a background ``_CameraScanDecodeThread`` runs the QR decode, and this main loop renders the
    preview (numpy -> RGB565 -> ``set_frame``) + overlay progress and pumps LVGL under
    ``renderer.lock`` at a steady ~camera-rate cadence. The per-frame sleep up to
    ``PREVIEW_INTERVAL`` yields the single core to the decode thread.

    Cancel is joystick LEFT / RIGHT via ``HardwareButtons``: the overlay is passive chrome (in
    hardware mode it shows a "< back" instruction line, per camera_preview_overlay.h), so the
    host owns the back affordance; nothing on the LVGL side emits a back event.

    Returns a ``ScanResult`` (the caller reads ``decoder`` + ``result.cancelled`` to route), or
    ``None`` if the camera fails to start so the caller can recover to a notice."""
    ensure_lvgl_runtime()
    # A fire-and-forget loading spinner (e.g. from the launching view) leaves a pump thread
    # running; stop it so it can't pump LVGL concurrently with this scan's pump (they'd fight
    # over the active screen). No-op if idle.
    stop_loading_pump()
    import time
    from seedsigner.gui.renderer import Renderer
    from seedsigner.hardware.buttons import HardwareButtons, HardwareButtonsConstants
    from seedsigner.hardware.camera import Camera, CameraConnectionError
    from seedsigner.hardware.scan_consumer import ScanResult
    from seedsigner.models.decode_qr import DecodeQRStatus

    # DecodeQRStatus -> overlay frame_status per camera_preview_set_progress's contract.
    _FRAME_STATUS = {
        DecodeQRStatus.PART_COMPLETE: 1,   # new part -> green dot
        DecodeQRStatus.PART_EXISTING: 2,   # already seen -> gray dot
        DecodeQRStatus.FALSE: 3,           # nothing decoded -> dot hidden
    }

    renderer = Renderer.get_instance()
    hw = HardwareButtons.get_instance()
    camera = Camera.get_instance()

    # 480x480 @ ~6fps RGB — the capture profile decode reliability is tuned for.
    # read_video_stream() returns the raw numpy frame the decoder expects.
    try:
        camera.start_video_stream_mode(resolution=(480, 480), framerate=6, format="rgb")
    except CameraConnectionError as e:
        logger.error("camera start failed for LVGL scan: %r", e)
        return None

    # Steady preview-cadence target (~camera rate). The per-frame sleep up to this interval is
    # what hands the single core to the decode thread: too short starves decode, too long makes
    # the preview lag the camera.
    PREVIEW_INTERVAL = 0.15

    cancelled = False
    complete = False
    decode_thread = None
    try:
        # A live preview isn't LVGL "input activity", so suspend the idle screensaver for the
        # scan (0 disables; runtime-updatable), then restore in finally. The screen also carries
        # SS_OBJ_FLAG_NO_SCREENSAVER, and camera_preview_close() resets the idle clock so the next
        # screen still gets a full saver window. Inside the try so the camera-stop finally always
        # runs once the camera has started.
        if not allow_screensaver:
            _lv.set_screensaver_timeout(0)

        # Build the preview screen + install the flush callback under the lock, then paint the
        # first frame (black sink + instruction overlay).
        with renderer.lock:
            _lv.set_flush_mode("python")
            _lv.set_flush_callback(_make_flush_callback(renderer.disp))
            _lv.clear_result_queue()
            cfg = {"allow_screensaver": allow_screensaver}
            if instructions_text:
                cfg["instructions_text"] = instructions_text
            _lv.camera_preview_screen(cfg)
            _lv.lvgl_pump(5, 1)

        # Decode runs on the worker thread; this loop renders the preview, pumps LVGL, reads cancel.
        decode_thread = _CameraScanDecodeThread(camera, decoder)
        decode_thread.start()

        while not decode_thread.done:
            t_start = time.monotonic()

            # Cancel: joystick LEFT / RIGHT (the host-wired back affordance).
            if hw.check_for_low(keys=[HardwareButtonsConstants.KEY_LEFT,
                                      HardwareButtonsConstants.KEY_RIGHT]):
                cancelled = True
                break

            frame = camera.read_video_stream()
            if frame is not None:
                # Snapshot the decode thread's latest progress (plain scalar reads).
                pct = decode_thread.percent
                fs = _FRAME_STATUS.get(decode_thread.status, 0)
                rgb565 = _numpy_rgb_to_rgb565(frame, camera._camera_rotation)
                with renderer.lock:
                    _lv.camera_preview_set_frame(rgb565)
                    # Keep the instruction line until there's real progress; once decoding,
                    # set_progress raises the bar + status dot (and implies scanning).
                    if pct > 0:
                        _lv.camera_preview_set_progress(pct, fs)
                    _lv.lvgl_pump(5, 1)
            else:
                # Camera still warming up: keep the overlay animating, don't render a None frame.
                with renderer.lock:
                    _lv.lvgl_pump(5, 1)

            if camera._video_stream is None:
                # Stream torn down out from under us (defensive).
                break

            # Pace to ~camera rate; the sleep hands the core to the decode thread.
            time.sleep(max(0.0, PREVIEW_INTERVAL - (time.monotonic() - t_start)))

        complete = decode_thread.complete
        if complete:
            # Snap the bar to full + green as a confirmation beat before teardown.
            with renderer.lock:
                _lv.camera_preview_set_progress(100, 1)
                _lv.lvgl_pump(5, 1)
    finally:
        if decode_thread is not None:
            decode_thread.stop()
            # Bounded wait for the in-flight add_image() to finish (the compat.threading Thread
            # has no join()) so the decoder is quiescent before we read it below.
            for _ in range(80):
                if not decode_thread.is_alive():
                    break
                time.sleep(0.01)
        camera.stop_video_stream_mode()
        with renderer.lock:
            _lv.camera_preview_close()
            _lv.set_flush_callback(None)
        if not allow_screensaver:
            _lv.set_screensaver_timeout(_screensaver_timeout_ms)
        # Reset the shared HardwareButtons input timer so the next screen's inactivity/
        # screensaver clock starts fresh.
        hw.update_last_input_time()

    reason = "complete" if complete else ("cancelled" if cancelled else "invalid")
    return ScanResult(decoder, complete, cancelled, reason, polls=0, dropped_new=0)


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
    """Drive the native animated QR-display pipeline for a QRDisplayScreen (both platforms).

    The native ``qr_display_screen`` owns rendering + the brightness UI (hardware hints / touch
    slider) + the brightness tip. Python builds the initial cfg, then — for an animated
    (UR-fountain) QR — pushes successive frames via ``qr_display_set_frame`` at ~6 fps, holding
    while ``qr_display_is_tip_active()`` (so the pure first frames stay up), restarting the
    sequence when the tip stows, and persisting + restarting on a brightness change. Returns when
    the user exits (``qr_display_done``); the caller routes on its own fixed Destination and
    ignores the value (parity with the PIL QRDisplayScreen, which also returns nothing useful).

    One frame loop, two pump mechanics — ``run_lvgl_screen``'s split: MicroPython's native task
    pumps LVGL and owns the display, so this loop only polls; CPython / Pi Zero (blended display)
    pumps LVGL itself under ``renderer.lock`` and routes pixels through the PIL driver's flush
    callback. The Pi routes here rather than to the PIL ``QRDisplayScreen`` because the PIL
    screen reads GPIO through ``HardwareButtons`` — a second reader, independent of the native
    input gate, that treats a still-held key as *new* input, so a click held slightly too long
    skipped or instantly dismissed the QR (see docs/_integration/pi-pil-input-cutover-todo.md)."""
    ensure_lvgl_runtime()
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

    # The density slider is only meaningful for a fountain encoder whose fragment size the host
    # can vary (the animated PSBT / wallet-export flow); a fixed QR (SeedQR, xpub, address, ...)
    # has no adjustable density. Detect that capability by duck-typing set_px_per_module (only
    # BaseFountainQrEncoder has it); omit density_control otherwise so the native screen shows a
    # brightness-only panel. The native screen holds no strings, so the labels are handed over
    # already translated (mirrors brighter_text / darker_text).
    if hasattr(encoder, "set_px_per_module"):
        cfg["density_control"] = True
        cfg["initial_px_per_module"] = settings.get_value(SettingsConstants.SETTING__QR_DENSITY)
        # TRANSLATOR_NOTE: title above the animated-QR density slider
        cfg["density_text"] = _("Density")
        # TRANSLATOR_NOTE: label at the low-density end of the QR density slider (bigger modules, easier to scan)
        cfg["density_min_text"] = _("Min")
        # TRANSLATOR_NOTE: label at the high-density end of the QR density slider (more data per frame, smaller modules)
        cfg["density_max_text"] = _("Max")

    # A QR being read is not LVGL "input activity", so the idle screensaver would otherwise
    # bounce over it. Suspend it for the screen's duration (0 disables; runtime-updatable —
    # the overlay-manager contract), then restore. Same known follow-up as run_camera_scan:
    # after a display longer than the timeout the next screen may screensave immediately.
    if not allow_screensaver:
        _lv.set_screensaver_timeout(0)
    renderer = None
    try:
        if IS_MICROPYTHON:
            _lv.clear_result_queue()
            _lv.qr_display_screen(cfg)
        else:
            # CPython / Pi Zero blended display: LVGL only advances on a host pump, so the
            # frame loop below pumps under renderer.lock (PIL and LVGL never write the panel
            # concurrently) with pixels routed through the PIL driver's flush callback —
            # run_lvgl_screen's mechanics. A caller may hand over with a loading spinner
            # still animating on its background pump (e.g. "Signing..." ->
            # PSBTSignedQRDisplayView); this entry point bypasses the View.run_screen seam,
            # so stop it here. TRANSITIONAL: this branch collapses into the MicroPython one
            # at the Pi native display-pump cutover.
            stop_loading_pump()
            from seedsigner.gui.renderer import Renderer
            renderer = Renderer.get_instance()
            with renderer.lock:
                _lv.set_flush_mode("python")
                _lv.set_flush_callback(_make_flush_callback(renderer.disp))
                _lv.clear_result_queue()
                _lv.qr_display_screen(cfg)

        was_tip_active = False
        while True:
            if IS_MICROPYTHON:
                event = _lv.poll_for_result()
            else:
                with renderer.lock:
                    _lv.lvgl_pump(5, 1)
                    event = _lv.poll_for_result()
            if event is not None:
                result = _translate_event(event)
                if isinstance(result, QRBrightnessEvent):
                    # User adjusted brightness: persist it and restart so the pure first
                    # frames replay once the tip stows.
                    settings.set_value(SettingsConstants.SETTING__QR_BRIGHTNESS, result.value)
                    encoder.restart()
                    continue
                if isinstance(result, QRDensityEvent):
                    # User adjusted density: re-split the fountain at the new px/module and
                    # persist it. set_px_per_module rebuilds the encoder from part 0, so the
                    # frame loop below re-pushes the new fragments from the start.
                    encoder.set_px_per_module(result.value)
                    settings.set_value(SettingsConstants.SETTING__QR_DENSITY, result.value)
                    continue
                # Any other event is the user exiting the screen.
                if not IS_MICROPYTHON:
                    # Reset the PIL-side input timer so returning to a PIL screen doesn't
                    # immediately re-trigger the PIL screensaver (dies with HardwareButtons
                    # at its retirement).
                    from seedsigner.hardware.buttons import HardwareButtons
                    HardwareButtons.get_instance().update_last_input_time()
                return result

            if is_animated:
                tip_active = _lv.qr_display_is_tip_active()
                if was_tip_active and not tip_active:
                    # Tip just stowed -> rewind so the valuable pure first frames replay.
                    encoder.restart()
                if not tip_active:
                    _lv.qr_display_set_frame(_qr_frame_bytes(encoder.next_part()))
                was_tip_active = tip_active

            # ~6 fps, matching the PIL QRDisplayThread cadence.
            _sleep_ms(166)
    finally:
        if not IS_MICROPYTHON:
            _lv.set_flush_callback(None)
        if not allow_screensaver:
            _lv.set_screensaver_timeout(_screensaver_timeout_ms)


def run_camera_entropy(*, seed_hash=None):
    """Drive the native image-entropy capture pipeline on either hardware target.

    Mirrors ``run_camera_scan`` in both halves: the native ``camera_entropy`` module owns the
    live preview + overlay on both platforms, and — like every CPython flow — the Pi additionally
    installs the PIL flush callback and pumps LVGL itself (see ``_tick`` below), since it has no
    firmware display task. Python drives the documented host loop (see modcamera_entropy.c):

        start(seed_hash) -> live preview (poll frames_chained for progress) -> capture() ->
        poll get_result() -> (preview_frame_entropy, final_image_bytes) -> stop()  [or resume() to reshoot]

    The firmware chains the preview frames (SHA-256) EXCLUDING the latched final frame; the caller
    (ToolsImageEntropyMnemonicLengthView) derives the seed inline from
    (preview_frame_entropy, final_image_bytes) + host entropy.

    Returns the (preview_frame_entropy, final_image_bytes) bytes tuple on accept, or None on cancel
    / camera bring-up
    failure (the caller recovers to the previous screen). ``seed_hash`` is an optional 32-byte
    caller-uniqueness seed (NOT the entropy source — the camera frames are); the app passes None.

    Event mapping (confirmed on-device): the native overlay routes ALL three controls through the
    shared ``button_selected`` poll-queue event — the shutter and Accept both emit
    ``on_button_selected(0, ...)``, while the back arrow emits
    ``on_button_selected(SEEDSIGNER_RET_BACK_BUTTON, "back")``. So back is distinguished by the
    ``RET_CODE__BACK_BUTTON`` sentinel in the index slot, NOT by a distinct ``topnav_back`` kind
    (the ESP32 ``poll_for_result`` never emits that kind). During preview: back -> cancel (return
    None -> View), shutter -> capture(). During review: back -> reshoot (resume() -> preview),
    Accept -> return the frame. Test back BEFORE the generic button branch, or it gets swallowed.
    """
    ensure_lvgl_runtime()
    import camera_entropy
    from seedsigner.compat.l10n import gettext as _

    # On the Pi, LVGL pixels reach the panel only through the PIL driver's flush callback,
    # and LVGL only advances when Python pumps it — exactly as run_camera_scan. Missing
    # either does NOT merely stale the screen; it breaks three things at once, which is
    # why an unpumped flow reads as a total freeze: the render/flush, the native
    # camera_engine_pump_consume() hook (the only path moving captured frames into the
    # preview sink, so the preview stays blank however well the camera runs), and LVGL's
    # input read (so no key registers and the flow cannot be exited). MicroPython's
    # firmware display task owns rendering, so it neither installs a callback nor pumps.
    renderer = None
    if not IS_MICROPYTHON:
        from seedsigner.gui.renderer import Renderer
        renderer = Renderer.get_instance()

    def _tick(ms):
        """One idle iteration of a poll loop: advance LVGL, then wait.

        The three loops below are otherwise pure `poll_for_result()` spins, so this is
        the single place the Pi's pump lives — the CPython equivalent of the firmware
        display task. A no-op pump on MicroPython, where that task already runs.
        """
        if renderer is not None:
            with renderer.lock:
                _lv.lvgl_pump(5, 1)
        _sleep_ms(ms)

    # The camera preview isn't LVGL "input activity", so suspend the idle screensaver for the
    # capture's duration (0 disables; runtime-updatable), then restore — same as run_camera_scan.
    # TODO (same as run_camera_scan): move this into the native camera_entropy overlay via
    # SS_OBJ_FLAG_NO_SCREENSAVER, then drop the override and the outer try/finally that restores it.
    _lv.set_screensaver_timeout(0)
    try:
        if renderer is not None:
            with renderer.lock:
                _lv.set_flush_mode("python")
                _lv.set_flush_callback(_make_flush_callback(renderer.disp))
        # The native overlay holds no strings; hand it every label already translated. Nothing
        # is hardcoded in firmware, so these must be set before the camera starts or the
        # button/text render blank.
        #
        # All four are passed unconditionally, which is what both bindings are built for: args
        # 1-2 are the TOUCH affordances (the CAPTURING transient + the CONFIRM Accept button),
        # args 3-4 the HARDWARE-input bottom instruction lines the overlay renders only under
        # INPUT_MODE_HARDWARE — inert on a touch panel, so one host loop serves both platforms.
        # Composed from the same msgids as the PIL screens they replace
        # (ToolsImageEntropyLivePreviewScreen / ToolsImageEntropyFinalImageScreen), so the
        # wording and its translations carry over rather than forking a second set of strings.
        #
        # POSITIONAL, not keyword: the ESP binding is MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN,
        # which takes positional args only — keywords would work on the Pi and break the ESP.
        camera_entropy.set_labels(
            _("Capturing image..."),
            _("Accept"),
            "< " + _("back") + "  |  " + _("click a button"),
            "< " + _("reshoot") + "  |  " + _("accept") + " >",
        )
        try:
            camera_entropy.start(seed_hash)
        except OSError as e:
            # Native camera bring-up can fail; recover (None) instead of crashing.
            logger.error("camera_entropy.start() failed: %r", e)
            return None
        try:
            # Drop any stale UI event (e.g. the menu tap that launched us) so it can't be
            # misread as an immediate capture/cancel.
            _lv.clear_result_queue()
            while True:
                # --- Preview phase: collect frames until the user captures or cancels. ---
                captured = False
                while not captured:
                    event = _lv.poll_for_result()
                    if event is not None:
                        # The overlay's back arrow and its shutter BOTH arrive as
                        # "button_selected" on the poll queue (the native back_button emits
                        # on_button_selected(SEEDSIGNER_RET_BACK_BUTTON, "back")); the back
                        # press is distinguished only by the RET_CODE__BACK_BUTTON sentinel
                        # in the index slot. Test back FIRST — it's a subset of
                        # button_selected, so the generic branch would otherwise swallow it
                        # and mis-fire a capture.
                        if event[1] == RET_CODE__BACK_BUTTON:
                            return None  # cancelled during preview -> hand control back to the View
                        if event[0] == "button_selected":
                            camera_entropy.capture()
                            captured = True
                    else:
                        _tick(20)

                # --- Latch: wait for the frozen final frame to be ready + displayed. ---
                result = None
                while result is None:
                    result = camera_entropy.get_result()
                    if result is None:
                        # Pump here too: the CAPTURING transient and the frozen confirm
                        # frame are both painted by the native overlay during this wait.
                        _tick(5)
                preview_frame_entropy, final_image_bytes, _n = result

                # --- Review phase: accept the frozen frame, or reshoot (resume + loop). ---
                while True:
                    event = _lv.poll_for_result()
                    if event is not None:
                        # Same discrimination as preview: the back arrow (reshoot) shares the
                        # "button_selected" kind with the Accept button and is told apart only
                        # by the RET_CODE__BACK_BUTTON index sentinel. Test back FIRST so it
                        # can't be misread as an accept.
                        if event[1] == RET_CODE__BACK_BUTTON:
                            # Discarding this shot: overwrite the camera data, clearing it to
                            # zero, before dropping it. Every reshoot allocates a fresh pair on
                            # the next get_result(), so without this each one leaves a whole
                            # readable image behind.
                            camera_entropy.secure_zero(final_image_bytes)
                            camera_entropy.secure_zero(preview_frame_entropy)
                            preview_frame_entropy = None
                            final_image_bytes = None
                            gc.collect()

                            camera_entropy.resume()        # reshoot -> back to preview
                            break
                        if event[0] == "button_selected":
                            return (preview_frame_entropy, final_image_bytes)  # accept
                    else:
                        _tick(20)
        finally:
            camera_entropy.stop()
    finally:
        if renderer is not None:
            _lv.set_flush_callback(None)
        _lv.set_screensaver_timeout(_screensaver_timeout_ms)


def run_seed_address_verification_screen(*, address, type_network, network, title,
                                         skip_label, cancel_label,
                                         threadsafe_counter, verified_index,
                                         allow_screensaver=False):
    """Drive the native seed_address_verification_screen (MicroPython / ESP32).

    The native screen is a static address / type-network readout plus a live "Checking address
    N" progress line pushed via ``seed_address_verification_set_progress()``. The host owns the
    brute-force worker + match logic (the caller's ``threadsafe_counter`` / ``verified_index``);
    this loop only reflects them: build the screen once, then poll for Skip 10 / Cancel while
    pushing the progress line and watching ``verified_index`` for a match. Skip 10 bumps the
    worker's counter and keeps scanning; Cancel gives up. Returns True when the worker matched
    the address, False on Cancel. Mirrors ``run_qr_display_screen`` (build-and-return + a Python
    poll loop); the native screen forces show_back_button off, so the only buttons are Skip 10
    (index 0) and Cancel (index 1)."""
    ensure_lvgl_runtime()
    from seedsigner.compat.l10n import gettext as _

    def _progress_text():
        # TRANSLATOR_NOTE: Inserts the nth address number (e.g. "Checking address 7")
        return _("Checking address {}").format(threadsafe_counter.cur_count)

    cfg = {
        "top_nav": {"title": title},
        "address": address,
        "type_network": type_network,
        "network": network,
        "button_list": [skip_label, cancel_label],
        "progress_text": _progress_text(),
        "allow_screensaver": allow_screensaver,
    }

    # A scan-in-progress is not LVGL "input activity", so suspend the idle screensaver for the
    # screen's duration (mirrors run_qr_display_screen / run_camera_scan), then restore.
    if not allow_screensaver:
        _lv.set_screensaver_timeout(0)
    try:
        _lv.clear_result_queue()
        _lv.seed_address_verification_screen(cfg)
        while True:
            event = _lv.poll_for_result()
            if event is not None:
                result = _translate_event(event)
                if result == 0:
                    # Skip 10: jump the worker ahead and keep scanning.
                    threadsafe_counter.increment(10)
                    continue
                # Cancel (index 1): give up.
                return False
            if verified_index.cur_count is not None:
                return True
            _lv.seed_address_verification_set_progress(_progress_text())
            _sleep_ms(100)
    finally:
        if not allow_screensaver:
            _lv.set_screensaver_timeout(_screensaver_timeout_ms)
