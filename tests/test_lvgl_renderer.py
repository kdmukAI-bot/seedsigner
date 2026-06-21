"""Unit tests for the PIL-free `LvglRenderer` (`seedsigner.gui.lvgl_renderer`).

On stock MicroPython the PIL-based `Renderer` can't load, so `View._initialize()`
platform-selects `LvglRenderer` instead. These tests run on CPython and verify
the small surface the View layer and the LVGL screen runner touch, plus the
import-time PIL-freeness that is the whole point of the module.
"""
import sys

from seedsigner.gui.lvgl_renderer import (
    LvglRenderer,
    DEFAULT_CANVAS_WIDTH,
    DEFAULT_CANVAS_HEIGHT,
)


def teardown_function():
    # The singleton is process-global; reset it so tests don't leak instances.
    LvglRenderer._instance = None


def test_module_binds_no_pil_symbols():
    """The module must not pull PIL into its namespace (PIL can't exist on-device)."""
    import seedsigner.gui.lvgl_renderer as mod
    # The PIL-based Renderer binds these at module scope; this one must not.
    for pil_symbol in ("Image", "ImageDraw"):
        assert pil_symbol not in vars(mod)


def test_runner_surface_present():
    # The surface the LVGL screen runner + View read is present at class level.
    for attr in ("lock", "canvas_width", "canvas_height", "disp"):
        assert hasattr(LvglRenderer, attr)


def test_default_canvas_dimensions():
    assert LvglRenderer.canvas_width == DEFAULT_CANVAS_WIDTH == 240
    assert LvglRenderer.canvas_height == DEFAULT_CANVAS_HEIGHT == 240


def test_disp_is_none():
    # The native module owns the panel on-device; disp is only dereferenced
    # behind `not IS_MICROPYTHON` in the runner.
    assert LvglRenderer.disp is None


def test_is_not_screenshot_generator():
    LvglRenderer.configure_instance()
    assert LvglRenderer.get_instance().is_screenshot_generator is False


def test_singleton_contract():
    """ConfigurableSingleton: get_instance() needs a prior configure_instance()."""
    import pytest
    with pytest.raises(Exception):
        LvglRenderer.get_instance()
    LvglRenderer.configure_instance()
    a = LvglRenderer.get_instance()
    b = LvglRenderer.get_instance()
    assert a is b


def test_lock_is_a_real_lock():
    # compat.threading.Lock is a context manager on both runtimes.
    with LvglRenderer.lock:
        pass
