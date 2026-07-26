"""MicroSD mount/insertion behavior — in particular the ESP32 (MicroPython) path,
where the microSD is the only user-writable area and settings.json lives on it.

On ESP32 the /sd mount lifecycle (mount + hotplug unmount/remount) is owned by the
frozen ``seedsigner_lvgl_screens`` facade (D-8); the app delegates to it. That facade
is a native module absent on the CPython test host, so these tests inject a controllable
stand-in and assert the delegation, rather than exercising real machine/vfs.
"""
import sys
import types

import seedsigner.hardware.microsd as microsd_mod
from seedsigner.hardware.microsd import MicroSD
from seedsigner.models.settings_definition import SettingsConstants


def _bare_microsd():
    # A bare instance that skips BaseThread.__init__; we only exercise is_inserted.
    return object.__new__(MicroSD)


def _install_fake_facade(monkeypatch, *, ensure=True, live=True, poll=None):
    """Inject a stand-in for the frozen ``seedsigner_lvgl_screens`` facade (absent on the
    CPython host) so the app's ESP delegation can be exercised. Returns (module, calls)."""
    fake = types.ModuleType("seedsigner_lvgl_screens")
    calls = {"ensure": 0, "live": 0, "poll": 0}

    def sd_ensure():
        calls["ensure"] += 1
        return ensure

    def sd_live():
        calls["live"] += 1
        return live

    def sd_poll():
        calls["poll"] += 1
        return poll

    fake.sd_ensure = sd_ensure
    fake.sd_live = sd_live
    fake.sd_poll = sd_poll
    monkeypatch.setitem(sys.modules, "seedsigner_lvgl_screens", fake)
    return fake, calls


def test_microsd_mount_constant_is_sd():
    # The single source of truth the settings path and catalog root both build on.
    assert SettingsConstants.MICROSD_MOUNT == "/sd"


def test_ensure_mounted_is_noop_true_off_micropython(monkeypatch):
    # On CPython the card (if any) is mounted outside the app: ensure_mounted must be
    # a pure no-op that never touches the facade and always reports available.
    monkeypatch.setattr(microsd_mod, "IS_MICROPYTHON", False)
    assert MicroSD.ensure_mounted() is True


def test_ensure_mounted_delegates_to_facade_on_micropython(monkeypatch):
    # ESP32: mounting is the facade's job (single owner); ensure_mounted just delegates.
    monkeypatch.setattr(microsd_mod, "IS_MICROPYTHON", True)
    _, calls = _install_fake_facade(monkeypatch, ensure=True)
    assert MicroSD.ensure_mounted() is True
    assert calls["ensure"] == 1


def test_ensure_mounted_failsoft_when_facade_unavailable(monkeypatch):
    # If the facade import fails (e.g. very early boot), swallow to False — never a
    # traceback that would brick boot.
    monkeypatch.setattr(microsd_mod, "IS_MICROPYTHON", True)
    monkeypatch.setitem(sys.modules, "seedsigner_lvgl_screens", None)  # -> ImportError
    assert MicroSD.ensure_mounted() is False


def test_is_inserted_true_on_desktop(monkeypatch):
    # Desktop dev host: not seedsigner-os, not MicroPython → always available.
    monkeypatch.setattr(microsd_mod, "IS_MICROPYTHON", False)
    assert _bare_microsd().is_inserted is True


def test_is_inserted_tracks_facade_probe_on_micropython(monkeypatch):
    # On ESP32 is_inserted reflects the facade's honest liveness probe (sd_live), so a
    # physically-pulled card reads False even though its mount registration lingers.
    monkeypatch.setattr(microsd_mod, "IS_MICROPYTHON", True)
    _install_fake_facade(monkeypatch, live=True)
    assert _bare_microsd().is_inserted is True
    _install_fake_facade(monkeypatch, live=False)
    assert _bare_microsd().is_inserted is False


def test_poll_is_noop_off_micropython(monkeypatch):
    # No detection polling off MicroPython (Pi uses the mdev thread; desktop has nothing).
    monkeypatch.setattr(microsd_mod, "IS_MICROPYTHON", False)
    _, calls = _install_fake_facade(monkeypatch, poll="removed")
    MicroSD.poll()
    assert calls["poll"] == 0  # never even consulted the facade


def test_poll_no_dispatch_when_no_change(monkeypatch):
    monkeypatch.setattr(microsd_mod, "IS_MICROPYTHON", True)
    _, calls = _install_fake_facade(monkeypatch, poll=None)
    MicroSD.poll()
    assert calls["poll"] == 1  # probed, but nothing to dispatch


def _install_dispatch_fakes(monkeypatch):
    """Fake Settings / Controller / toast so poll()'s dispatch can be observed without
    importing the heavy real modules."""
    seen = {"action": None, "toast": None}

    settings_mod = types.ModuleType("seedsigner.models.settings")
    class _Settings:
        @staticmethod
        def handle_microsd_state_change(action):
            seen["action"] = action
    settings_mod.Settings = _Settings
    monkeypatch.setitem(sys.modules, "seedsigner.models.settings", settings_mod)

    toast_mod = types.ModuleType("seedsigner.gui.toast")
    class _Toast:
        def __init__(self, action):
            seen["toast"] = action
    toast_mod.SDCardStateChangeToastManagerThread = _Toast
    monkeypatch.setitem(sys.modules, "seedsigner.gui.toast", toast_mod)

    ctrl_mod = types.ModuleType("seedsigner.controller")
    class _Controller:
        _i = None
        @classmethod
        def get_instance(cls):
            if cls._i is None:
                cls._i = _Controller()
            return cls._i
        def activate_toast(self, toast):
            pass
    ctrl_mod.Controller = _Controller
    monkeypatch.setitem(sys.modules, "seedsigner.controller", ctrl_mod)
    return seen


def test_poll_dispatches_removal(monkeypatch):
    monkeypatch.setattr(microsd_mod, "IS_MICROPYTHON", True)
    _install_fake_facade(monkeypatch, poll="removed")
    seen = _install_dispatch_fakes(monkeypatch)
    MicroSD.poll()
    assert seen["action"] == MicroSD.ACTION__REMOVED
    assert seen["toast"] == MicroSD.ACTION__REMOVED


def test_poll_dispatches_insertion(monkeypatch):
    monkeypatch.setattr(microsd_mod, "IS_MICROPYTHON", True)
    _install_fake_facade(monkeypatch, poll="inserted")
    seen = _install_dispatch_fakes(monkeypatch)
    MicroSD.poll()
    assert seen["action"] == MicroSD.ACTION__INSERTED
    assert seen["toast"] == MicroSD.ACTION__INSERTED
