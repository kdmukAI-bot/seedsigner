"""
PIL-free ``Renderer`` stand-in for stock MicroPython (ESP32).

``gui/renderer.py`` imports PIL at module top and owns a PIL canvas — neither
exists on stock MicroPython. On-device LVGL owns the panel natively, so the
business logic needs only a tiny renderer surface: the bits ``View`` and the
LVGL screen runner actually touch.

``View._initialize()`` platform-selects between the two: the PIL ``Renderer`` on
CPython (Pi Zero, blended display), ``LvglRenderer`` on MicroPython. The surface
kept in sync with the real ``Renderer``:

  * ``lock``                    — held around every native render (``run_lvgl_screen``)
  * ``canvas_width``/``canvas_height`` — read by ``View._initialize()`` into the View
  * ``disp``                    — ``None``; only dereferenced behind ``not IS_MICROPYTHON``
  * ``is_screenshot_generator`` — always False (no screenshot generator on-device)

It deliberately holds no drawing surface and no display driver: the native
``seedsigner_lvgl_screens`` module flushes pixels itself.
"""
from seedsigner.compat.threading import Lock
from seedsigner.models.singleton import ConfigurableSingleton


# The LVGL screens are authored for a 240x240 canvas (matching the Pi Zero ST7789
# panel); the ESP32 board's native panel resolution is configured inside the
# native module, not here.
DEFAULT_CANVAS_WIDTH = 240
DEFAULT_CANVAS_HEIGHT = 240



class LvglRenderer(ConfigurableSingleton):
    canvas_width = DEFAULT_CANVAS_WIDTH
    canvas_height = DEFAULT_CANVAS_HEIGHT
    disp = None
    lock = Lock()


    @property
    def is_screenshot_generator(self) -> bool:
        return False


    @classmethod
    def configure_instance(cls):
        # Instantiate the one and only LvglRenderer instance. No display setup:
        # the native seedsigner_lvgl_screens module owns the panel on-device.
        renderer = object.__new__(cls)

        # Learn the REAL panel dimensions from the native display profile so the
        # resolution-aware QR density lookup (models/qr_density.py) keys on the actual screen
        # instead of the 240x240 default -- otherwise a 480px panel silently uses the 240 row.
        # display_size() is a new platform binding (added alongside the on_qr_density slider);
        # until every build ships it, fall back to the 240 default when it is absent or raises
        # so older firmware / the .so still boot. Only canvas_height drives density; width is
        # populated too since the getter returns both.
        try:
            size = cls._native_display_size()
            if size:
                width, height = size
                if width and height:
                    cls.canvas_width = width
                    cls.canvas_height = height
        except Exception:
            pass

        cls._instance = renderer


    @staticmethod
    def _native_display_size():
        """``(width, height)`` from the native ``display_size()`` binding, or ``None``.

        Imported the same way as gui/lvgl_screen_runner (``seedsigner_lvgl_screens``, with the
        pre-rename ``seedsigner_lvgl`` fallback). Returns ``None`` when the native module or
        the binding is absent -- dev/CI hosts, or a platform build predating the binding."""
        try:
            import seedsigner_lvgl_screens as lv
        except ImportError:
            try:
                import seedsigner_lvgl as lv
            except ImportError:
                return None
        display_size = getattr(lv, "display_size", None)
        if display_size is None:
            return None
        return display_size()
