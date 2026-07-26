"""
Runs LVGL C-module screens within SeedSigner's View/Screen architecture — the one
place the Python app drives the native ``seedsigner_lvgl_screens`` module.

A View runs an LVGL screen by passing its native screen-function *name* to
``View.run_screen`` (the dispatch seam), which forwards here. The screen is
identified by name — never by importing the native module into a View — so the
business logic stays free of any ``seedsigner_lvgl_screens`` dependency and the
flow-test harness (which patches ``run_screen``) never touches the native path.

One render model on both platforms: the native module owns the display and pumps
LVGL on a background task (the ESP32 firmware's display task; the Pi's native pump
thread), so there is no Python flush callback and no Python-side pump. The native
screen function is a pure builder (it builds the widget tree and returns immediately)
and a Python loop polls for the result.

Screensaver: the native overlay manager owns the idle screensaver on both
platforms. A C dispatcher watches the LVGL inactivity timer and swaps to / restores
from the bouncing-logo screensaver entirely in C — nothing in Python drives it. The
global timeout is handed to the native side once at init (``set_screensaver_timeout``).
Per-screen opt-out rides in the cfg as ``allow_screensaver``: a View sets it False
for screens that must stay up (e.g. camera scanning), the shared parser defaults it
true, and the native scaffold stamps the screen object so the dispatcher skips it.
"""
import gc
import logging

from seedsigner.compat import IS_MICROPYTHON
from seedsigner.compat.threading import Lock
from seedsigner.compat.time import sleep_ms as _sleep_ms
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
            # seedsigner_lvgl_screens.
            import seedsigner_lvgl as lv
        if IS_MICROPYTHON:
            # On-device the native module sets up display + input itself.
            lv.init()
        else:
            # Pi Zero (CPython): bring up the native ST7789 panel (SPI + GPIO) before LVGL,
            # so the background pump thread's native flush has an initialized backend to
            # paint into (an un-brought-up backend no-op-paints, leaving the panel blank).
            # native_display_init also claims the native input lines (no separate
            # native_input_init call needed). The resolution comes from the Pi-only
            # SETTING__DISPLAY_RESOLUTION ("{width}x{height}"); Settings is up well before
            # this init (see the camera-rotation note below).
            from seedsigner.models.settings import Settings, SettingsConstants
            resolution = Settings.get_instance().get_value(
                SettingsConstants.SETTING__DISPLAY_RESOLUTION, default_if_none=True)
            width, height = (int(dimension) for dimension in resolution.split("x"))
            lv.native_display_init(width=width, height=height)
            lv.lvgl_init(hor_res=width, ver_res=height)

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
    crash or block its caller. The binding is platform-symmetric.
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
    clears its own toast).
    """
    try:
        ensure_lvgl_runtime()
    except ImportError:
        return
    try:
        _lv.dismiss_toast()
    except Exception:
        logger.exception("dismiss_toast failed")


def get_inactive_time_ms():
    """Milliseconds since the last input activity on the LVGL display, or ``None`` when the
    native runtime is absent (dev/CI/host tests).

    Thin host wrapper over the native ``get_inactive_time_ms()``: any keypad press resets the
    display's activity clock toward 0, so a small value means the user just interacted. It
    backs the toast pre-show activation-delay cancel (a fresh press during the pre-show window
    cancels the pending toast). Presses only register while the runtime is pumped,
    so the reading is meaningful only over a live native screen (whose runner pumps) — which
    is exactly the context a toast shows in.
    """
    try:
        ensure_lvgl_runtime()
    except ImportError:
        return None
    try:
        return _lv.get_inactive_time_ms()
    except Exception:
        logger.exception("get_inactive_time_ms failed")
        return None


def run_lvgl_screen(screen, *, attrs=None):
    """Run an LVGL screen and return its result.

    Args:
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

    # The native screen fn is a pure builder — it builds the widget tree and returns
    # immediately — and the native pump task owns the display, processes input, and
    # runs the idle screensaver; this loop only builds once and polls for the result.
    _lv.clear_result_queue()
    screen_fn(*args)
    from seedsigner.hardware.microsd import MicroSD
    while True:
        event = _lv.poll_for_result()
        if event is not None:
            return _translate_event(event)
        # microSD hotplug tick — this poll loop is the only frequent Python cadence on
        # ESP32, which has no card-detect GPIO and so detects insert/remove by polling.
        # A no-op off MicroPython (Linux uses mdev), and self-throttled inside the facade,
        # so calling it every iteration is cheap; GIL-serialized with all SD I/O here.
        MicroSD.poll()
        _sleep_ms(20)


def run_loading_screen(text=None):
    """Show the native self-animating loading spinner (fire-and-forget).

    Unlike ``run_lvgl_screen``, the loading screen produces no terminal event and is NOT
    polled: it is a pure builder that returns immediately. The native pump task animates
    the spinner on its own while the calling thread blocks in a long task; dismiss it
    simply by loading the next screen (its ``LV_EVENT_DELETE`` frees the timer). There is
    no ``stop()`` at the call site.

    Degrades to a no-op when the native runtime is absent (dev/CI, ``ImportError``) or when
    the deployed firmware/.so predates the ``loading_spinner_screen`` binding, so the app change is
    safe against whatever binary is currently on-device.
    """
    try:
        ensure_lvgl_runtime()
    except ImportError:
        return  # native module absent (dev/CI)
    if not hasattr(_lv, "loading_spinner_screen"):
        return  # deployed firmware/.so predates the binding
    cfg = {"text": text} if text else None
    _lv.clear_result_queue()
    _lv.loading_spinner_screen(cfg)


def clear_screen():
    """Blank the display to black — the app's parting frame on exit.

    Loads an all-black LVGL screen and pushes it to the panel via the native clear_screen
    binding (raspi py_clear_screen -> lvgl_clear_to_black); the native pump task owns the
    display on both platforms. A no-op when the native runtime is absent (dev/CI,
    ImportError) or the deployed .so/firmware predates the clear_screen binding (the ESP32
    firmware does not bind it yet), so it is safe against whatever binary is on-device.
    """
    try:
        ensure_lvgl_runtime()
    except ImportError:
        return  # native module absent (dev/CI)
    if not hasattr(_lv, "clear_screen"):
        return  # deployed .so/firmware predates the binding
    _lv.clear_screen()


def _make_scan_should_continue():
    """Build the ``should_continue`` callable that ends a scan on user cancel.

    During a scan the native camera overlay's back button (and, on hardware, the
    joystick back/LEFT press) is the only producer feeding the shared UI event queue,
    so any event drained here — surfaced as a button_selected carrying the
    RET_CODE__BACK_BUTTON sentinel — means the user backed out. Returns False to cancel.
    The native pump task renders and reads input on its own, so this only drains.
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
    return drain


def run_camera_scan(decoder, *, instructions_text=None):
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

    ``instructions_text`` is the localized hardware-mode overlay line (e.g.
    "< back  |  Scan a QR code", built by ``scan_instructions_line``); it is handed to
    ``camera_scanner.start`` so the overlay shows the back affordance + hint on entry.
    None leaves the instruction slot empty. On the ESP's touch UI (persistent gutter
    back button) the text is unused; start() accepts the kwarg there for contract parity.

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
    _lv.set_screensaver_timeout(0)
    try:
        try:
            camera_scanner.start(instructions_text=instructions_text)
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
    """Drive the native animated QR-display pipeline for a QRDisplayScreen (both platforms).

    The native ``qr_display_screen`` owns rendering + the brightness UI (hardware hints / touch
    slider) + the brightness tip. Python builds the initial cfg, then — for an animated
    (UR-fountain) QR — pushes successive frames via ``qr_display_set_frame`` at ~6 fps, holding
    while ``qr_display_is_tip_active()`` (so the pure first frames stay up), restarting the
    sequence when the tip stows, and persisting + restarting on a brightness change. Returns when
    the user exits (``qr_display_done``); the caller routes on its own fixed Destination and
    ignores the value (parity with the PIL QRDisplayScreen, which also returns nothing useful).

    The native pump task owns the display on both platforms, so this frame loop only builds,
    pushes frames, and polls."""
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
                if isinstance(result, QRDensityEvent):
                    # User adjusted density: re-split the fountain at the new px/module and
                    # persist it. set_px_per_module rebuilds the encoder from part 0, so the
                    # frame loop below re-pushes the new fragments from the start.
                    encoder.set_px_per_module(result.value)
                    settings.set_value(SettingsConstants.SETTING__QR_DENSITY, result.value)
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

            # ~6 fps, matching the PIL QRDisplayThread cadence.
            _sleep_ms(166)
    finally:
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

    def _tick(ms):
        """One idle iteration of a poll loop: the native pump task advances LVGL and
        moves captured frames into the preview sink, so this only waits."""
        _sleep_ms(ms)

    # The camera preview isn't LVGL "input activity", so suspend the idle screensaver for the
    # capture's duration (0 disables; runtime-updatable), then restore — same as run_camera_scan.
    # TODO (same as run_camera_scan): move this into the native camera_entropy overlay via
    # SS_OBJ_FLAG_NO_SCREENSAVER, then drop the override and the outer try/finally that restores it.
    _lv.set_screensaver_timeout(0)
    try:
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
        _lv.set_screensaver_timeout(_screensaver_timeout_ms)


def run_io_test_screen():
    """Drive the native hardware I/O self-test (Python provenance: IOTestScreen).

    Unlike the passive screens, io_test_screen owns its own keypad input: it reads the
    joystick/keys itself and flashes whichever control the user actuates, forwarding only
    KEY1/KEY2/KEY3 to this loop as ("aux_key", 0, "KEYn") poll-queue results. It emits no
    terminal navigation result — the host runs the self-test loop and reaps the screen by
    navigating on. Mirrors Python IOTestScreen._run:

        KEY1 -> "Capturing image..." band, grab one still + show it behind the chrome,
                then latch KEY2 = "Clear"  (io_test_set_capture_state CAPTURING -> CAPTURED)
        KEY2 -> clear the captured still                        (-> CAPTURE_IDLE)
        KEY3 -> exit the self-test

    Pi-hardware only (physical joystick + three keys), so the native io_test_screen forces
    INPUT_MODE_HARDWARE; the caller keeps it off the ESP/touch build (which binds no
    io_test_screen). Returns None; the caller navigates on.
    """
    ensure_lvgl_runtime()
    from seedsigner.compat.l10n import gettext as _

    # io_test_screen capture-state ints (mirror seedsigner.h io_test_capture_state_t): the
    # host reflects its single-frame grab back into the running screen.
    CAPTURE_IDLE, CAPTURE_CAPTURING, CAPTURE_CAPTURED = 0, 1, 2
    # KEY1 camera grab timing. The background pump feeds camera frames asynchronously, so the
    # grab waits for the first real frame (io_test_camera_frame_ready reflects the pump's
    # stash) before freezing:
    #   * FRAME_WAIT_TICKS bounds that wait. The libcamera cold-start's first delivered frame
    #     can exceed ~500ms on the Pi Zero OV5647; if the wait elapses with no frame the grab
    #     is treated as failed (plane stays dark).
    #   * SETTLE_TICKS then lets a few more frames flow so the frozen still reflects settled
    #     exposure (AE/AWB), not the dim first frame.
    FRAME_WAIT_TICKS = 150   # up to ~3s for the first delivered frame
    SETTLE_TICKS = 20        # ~0.4s more after the first frame for exposure to settle

    # title -> top_nav.title; the band + KEY labels pass straight through. Composed from the
    # same msgids as the PIL IOTestScreen, so the wording and its translations carry over.
    cfg = _assemble_cfg({
        "title": _("I/O Test"),
        "capturing_text": _("Capturing image..."),
        "clear_label": _("Clear"),
        "exit_label": _("Exit"),
    })

    def _tick(ms):
        """One idle iteration of the poll loop: the native pump task advances LVGL and
        feeds camera frames into the screen's plane, so this only waits."""
        _sleep_ms(ms)

    # Drop any stale UI event (e.g. the menu press that launched us) so it can't be
    # misread as a KEY press.
    _lv.clear_result_queue()
    _lv.io_test_screen(cfg)

    while True:
        event = _lv.poll_for_result()
        if event is None:
            _tick(20)
            continue
        kind, _index, label = event
        if kind != "aux_key":
            # io_test_screen forwards only KEY1/2/3; ignore anything else on the queue.
            continue
        if label == "KEY1":
            # Grab a still and show it behind the chrome. io_test_camera_start feeds the
            # screen's camera plane from the native engine; the background pump stashes each
            # frame (camera_engine_pump_consume) and io_test_camera_stop freezes the last.
            # The plane + its dims + the blit are owned by the screen (SCREENS-9) — the app
            # only starts/stops the feed and paces the grab: wait for the pump to stash the
            # first frame (bounded by FRAME_WAIT_TICKS), then let exposure settle before
            # freezing.
            _lv.io_test_set_capture_state(CAPTURE_CAPTURING)   # "Capturing…" band up
            captured = False
            try:
                _lv.io_test_camera_start()
                waited = 0
                while not _lv.io_test_camera_frame_ready() and waited < FRAME_WAIT_TICKS:
                    _tick(20)
                    waited += 1
                if _lv.io_test_camera_frame_ready():
                    # First frame arrived; let a few more flow so the still isn't the dim
                    # first frame (AE/AWB still settling).
                    for _ in range(SETTLE_TICKS):
                        _tick(20)
                    captured = True
                # else: timed out with no frame — treat as a failed grab (plane stays dark).
            except OSError as e:
                # Camera bring-up failed: leave the square dark, nothing captured.
                logger.error("io_test camera grab failed: %r", e)
            finally:
                _lv.io_test_camera_stop()   # freeze the last frame (idempotent)
            # Band down; KEY2 -> "Clear" on a real capture, blank if the grab failed.
            _lv.io_test_set_capture_state(CAPTURE_CAPTURED if captured else CAPTURE_IDLE)
        elif label == "KEY2":
            _lv.io_test_set_capture_state(CAPTURE_IDLE)       # clear the still
        elif label == "KEY3":
            return  # exit -> caller navigates on, reaping the screen


def run_seed_address_verification_screen(*, address, type_network, network, title,
                                         skip_label, cancel_label,
                                         threadsafe_counter, verified_index,
                                         allow_screensaver=False):
    """Drive the native seed_address_verification_screen (both platforms).

    The native screen is a static address / type-network readout plus a live "Checking address
    N" progress line pushed via ``seed_address_verification_set_progress()``. The host owns the
    brute-force worker + match logic (the caller's ``threadsafe_counter`` / ``verified_index``);
    this loop only reflects them: build the screen once, then poll for Skip 10 / Cancel while
    pushing the progress line and watching ``verified_index`` for a match. Skip 10 bumps the
    worker's counter and keeps scanning; Cancel gives up. Returns True when the worker matched
    the address, False on Cancel. The native screen forces show_back_button off, so the only
    buttons are Skip 10 (index 0) and Cancel (index 1).

    The native pump task owns the display on both platforms, so this loop only builds and
    polls."""
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
                if _translate_event(event) == 0:
                    # Skip 10: jump the worker ahead and keep scanning.
                    threadsafe_counter.increment(10)
                    continue
                # Cancel (index 1): give up.
                matched = False
                break
            if verified_index.cur_count is not None:
                matched = True
                break
            _lv.seed_address_verification_set_progress(_progress_text())
            _sleep_ms(100)

        return matched
    finally:
        if not allow_screensaver:
            _lv.set_screensaver_timeout(_screensaver_timeout_ms)
