"""Tests for the app-side camera wiring in ensure_lvgl_runtime().

Covers aliasing the native camera submodules to their top-level names so the drive
loops' bare imports work on the Pi.
"""
import sys

from unittest.mock import MagicMock

import pytest

# base sets up the sys.modules mocks that make seedsigner importable on the host.
from base import BaseTest

from seedsigner.gui import lvgl_screen_runner


@pytest.fixture
def fake_native_runtime(monkeypatch):
    """Stand a fake seedsigner_lvgl_screens in for the extension and reset the runner's
    cached handle so ensure_lvgl_runtime() actually re-initializes."""
    lv = MagicMock()
    lv.camera_scanner = MagicMock(name="camera_scanner")
    lv.camera_entropy = MagicMock(name="camera_entropy")

    monkeypatch.setitem(sys.modules, "seedsigner_lvgl_screens", lv)
    monkeypatch.setattr(lvgl_screen_runner, "_lv", None)
    monkeypatch.delitem(sys.modules, "camera_scanner", raising=False)
    monkeypatch.delitem(sys.modules, "camera_entropy", raising=False)

    # The aliases land in the real sys.modules, so make sure they don't leak.
    yield lv
    for name in ("camera_scanner", "camera_entropy"):
        sys.modules.pop(name, None)


def _force_fresh_init(monkeypatch):
    """Drop any runtime the BackgroundImportThread warm-up already brought up against the
    fake, so the next ensure_lvgl_runtime() re-runs the init body deterministically rather
    than returning early on a handle some other thread published."""
    monkeypatch.setattr(lvgl_screen_runner, "_lv", None)
    for name in ("camera_scanner", "camera_entropy"):
        sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# ensure_lvgl_runtime: camera submodule aliases
# ---------------------------------------------------------------------------

class TestCameraModuleAliases(BaseTest):
    def test_aliases_the_camera_submodules(self, fake_native_runtime, monkeypatch):
        """On the Pi the camera modules are submodules of the extension, but the drive
        loops do a bare `import camera_scanner` so one shape serves both platforms."""
        _force_fresh_init(monkeypatch)
        lvgl_screen_runner.ensure_lvgl_runtime()

        assert sys.modules["camera_scanner"] is fake_native_runtime.camera_scanner
        assert sys.modules["camera_entropy"] is fake_native_runtime.camera_entropy


    def test_alias_never_shadows_an_existing_module(self, fake_native_runtime, monkeypatch):
        """A real top-level module (the ESP case) or an injected test double must win."""
        _force_fresh_init(monkeypatch)
        already_there = MagicMock(name="real_camera_scanner")
        monkeypatch.setitem(sys.modules, "camera_scanner", already_there)

        lvgl_screen_runner.ensure_lvgl_runtime()

        assert sys.modules["camera_scanner"] is already_there


    def test_missing_submodule_is_left_to_the_drive_loops(self, fake_native_runtime, monkeypatch):
        """A no-camera diagnostic build has neither submodule; that should degrade to the
        drive loops' ImportError path, not raise AttributeError inside init."""
        del fake_native_runtime.camera_scanner
        del fake_native_runtime.camera_entropy

        _force_fresh_init(monkeypatch)
        lvgl_screen_runner.ensure_lvgl_runtime()

        assert "camera_scanner" not in sys.modules
        assert "camera_entropy" not in sys.modules
