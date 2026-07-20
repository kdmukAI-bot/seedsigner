"""Tests for the app-side camera wiring in ensure_lvgl_runtime().

Two things it has to get right: delivering SETTING__CAMERA_ROTATION to the native
engines (which read a sticky rotation rather than taking one per start(), so the app
pushes at init — covering the default and a restored settings.json — and again on every
change), and aliasing the native camera submodules to their top-level names so the drive
loops' bare imports work on the Pi. On MicroPython the rotation setting stays visible but
routes to a notice instead of its options list.
"""
import sys

from unittest.mock import MagicMock, patch

import pytest

# base sets up the sys.modules mocks that make seedsigner importable on the host.
from base import BaseTest

from seedsigner.gui import lvgl_screen_runner
from seedsigner.models.settings import Settings
from seedsigner.models.settings_definition import SettingsConstants, SettingsDefinition
from seedsigner.views import settings_views


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


# ---------------------------------------------------------------------------
# Camera rotation: default, init-time push, and per-change push
# ---------------------------------------------------------------------------

def test_default_rotation_is_zero_delta():
    """The native engines rotate clockwise off a 90-degree sensor base that is already
    correct for the default build, so the app's delta defaults to none. (The retired PIL
    path rotated counter-clockwise, where the equivalent default was 180.)"""
    entry = SettingsDefinition.get_settings_entry(SettingsConstants.SETTING__CAMERA_ROTATION)
    assert entry.default_value == SettingsConstants.CAMERA_ROTATION__0


class TestInitRotationPush(BaseTest):
    def test_pushes_the_current_rotation_at_init(self, fake_native_runtime, monkeypatch):
        Settings.get_instance().set_value(
            SettingsConstants.SETTING__CAMERA_ROTATION, SettingsConstants.CAMERA_ROTATION__270)

        _force_fresh_init(monkeypatch)
        fake_native_runtime.set_camera_rotation.reset_mock()
        lvgl_screen_runner.ensure_lvgl_runtime()

        # Raw setting value: the native layer composes the sensor-mount base itself.
        fake_native_runtime.set_camera_rotation.assert_called_once_with(270)


class TestSetValuePush(BaseTest):
    def test_change_is_pushed_to_the_native_engines(self, fake_native_runtime, monkeypatch):
        _force_fresh_init(monkeypatch)
        lvgl_screen_runner.ensure_lvgl_runtime()
        fake_native_runtime.set_camera_rotation.reset_mock()

        Settings.get_instance().set_value(
            SettingsConstants.SETTING__CAMERA_ROTATION, SettingsConstants.CAMERA_ROTATION__180)

        fake_native_runtime.set_camera_rotation.assert_called_once_with(180)


    def test_no_op_before_the_runtime_is_up(self):
        """At boot this runs while settings.json is being read. It must neither raise nor
        force LVGL init from inside Settings construction — the init-time push delivers
        the settled value once the runtime legitimately comes up."""
        with patch.object(lvgl_screen_runner, "_lv", None):
            with patch.object(lvgl_screen_runner, "ensure_lvgl_runtime") as mock_ensure:
                Settings.get_instance().set_value(
                    SettingsConstants.SETTING__CAMERA_ROTATION,
                    SettingsConstants.CAMERA_ROTATION__90)

        mock_ensure.assert_not_called()
        assert Settings.get_instance().get_value(
            SettingsConstants.SETTING__CAMERA_ROTATION) == SettingsConstants.CAMERA_ROTATION__90


# ---------------------------------------------------------------------------
# MicroPython: visible in the menu, inert when selected
# ---------------------------------------------------------------------------

def test_rotation_is_disabled_only_on_micropython():
    attr_name = SettingsConstants.SETTING__CAMERA_ROTATION

    with patch.object(settings_views, "IS_MICROPYTHON", True):
        assert settings_views._is_disabled_on_this_hardware(attr_name) is True

    with patch.object(settings_views, "IS_MICROPYTHON", False):
        assert settings_views._is_disabled_on_this_hardware(attr_name) is False


def test_other_settings_are_never_disabled():
    with patch.object(settings_views, "IS_MICROPYTHON", True):
        assert settings_views._is_disabled_on_this_hardware(
            SettingsConstants.SETTING__LOCALE) is False


class TestSettingsEntryDisabledView(BaseTest):
    def test_returns_to_the_settings_menu(self):
        view = settings_views.SettingsEntryDisabledView(
            attr_name=SettingsConstants.SETTING__CAMERA_ROTATION)

        with patch.object(settings_views.View, "run_button_list_screen",
                          return_value=0) as mock_screen:
            destination = view.run()

        assert destination.View_cls == settings_views.SettingsMenuView

        # The setting's name is surfaced so the notice says which one is unavailable.
        cfg = mock_screen.call_args.kwargs
        assert "Camera rotation" in cfg["text"]
        assert len(cfg["button_data"]) == 1
