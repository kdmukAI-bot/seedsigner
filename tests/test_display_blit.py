"""Unit tests for `blit_rgb565` — the raw RGB565 region write LVGL uses to render
through SeedSigner's existing display drivers (the blended-display path).

The drivers import `spidev` / `RPi.GPIO`, which are absent off-device, so they are
stubbed in `sys.modules` before import (the same approach as `tests/base.py`).
Instances are built via `__new__` to bypass hardware init; only the
coordinate-translation contract and the SPI write are exercised.
"""
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("spidev", MagicMock())
sys.modules.setdefault("RPi", MagicMock())
sys.modules.setdefault("RPi.GPIO", MagicMock())

from seedsigner.hardware.displays.display_driver import BaseDisplayDriver
from seedsigner.hardware.displays.ST7789 import ST7789
from seedsigner.hardware.displays.st7789_mpy import ST7789 as ST7789Mpy


def test_base_driver_blit_rgb565_not_implemented():
    driver = BaseDisplayDriver.__new__(BaseDisplayDriver)
    with pytest.raises(NotImplementedError):
        driver.blit_rgb565(0, 0, 1, 1, b"\x00\x00")


def test_st7789_blit_rgb565_uses_exclusive_end_coords():
    # ST7789.SetWindows expects exclusive end coords, so blit must add 1.
    driver = ST7789.__new__(ST7789)
    driver._dc = object()
    driver._spi = MagicMock()
    with patch.object(driver, "SetWindows") as mock_set_windows:
        driver.blit_rgb565(10, 20, 30, 40, b"\x12\x34")
    mock_set_windows.assert_called_once_with(10, 20, 31, 41)
    driver._spi.writebytes2.assert_called_once_with(b"\x12\x34")


def test_st7789_mpy_blit_rgb565_uses_inclusive_end_coords():
    # st7789_mpy._set_window expects inclusive end coords — passed through as-is.
    driver = ST7789Mpy.__new__(ST7789Mpy)
    driver.dc = object()
    driver.spi = MagicMock()
    with patch.object(driver, "_set_window") as mock_set_window:
        driver.blit_rgb565(10, 20, 30, 40, b"\x12\x34")
    mock_set_window.assert_called_once_with(10, 20, 30, 40)
    driver.spi.writebytes2.assert_called_once_with(b"\x12\x34")
