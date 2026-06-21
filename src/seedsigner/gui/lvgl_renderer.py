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
        cls._instance = renderer
