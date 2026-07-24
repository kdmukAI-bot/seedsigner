"""MicroSD mount/insertion behavior — in particular the ESP32 (MicroPython) path,
where the microSD is the only user-writable area and settings.json lives on it.

These exercise the real MicroSD class directly (not the BaseTest mock), so they
construct a bare instance and read the property/classmethod without spinning up the
detection thread.
"""
import seedsigner.hardware.microsd as microsd_mod
from seedsigner.hardware.microsd import MicroSD
from seedsigner.models.settings_definition import SettingsConstants


def _bare_microsd():
    # A bare instance that skips BaseThread.__init__; we only exercise is_inserted.
    return object.__new__(MicroSD)


def test_microsd_mount_constant_is_sd():
    # The single source of truth the settings path and catalog root both build on.
    assert SettingsConstants.MICROSD_MOUNT == "/sd"


def test_ensure_mounted_is_noop_true_off_micropython():
    # On CPython the card (if any) is mounted outside the app: ensure_mounted must be
    # a pure no-op that never touches machine/vfs and always reports available.
    assert MicroSD.ensure_mounted() is True


def test_ensure_mounted_failsoft_on_micropython_without_hardware(monkeypatch):
    # Simulate the MicroPython branch on a host with no SD hardware: os.stat on the
    # (bogus) mount fails, the machine.SDCard mount then raises, and it must be
    # swallowed to False — never propagate a traceback that would brick boot.
    monkeypatch.setattr(microsd_mod, "IS_MICROPYTHON", True)
    monkeypatch.setattr(SettingsConstants, "MICROSD_MOUNT", "/definitely-not-a-mount/sd")
    assert MicroSD.ensure_mounted() is False


def test_is_inserted_true_on_desktop():
    # Desktop dev host: not seedsigner-os, not MicroPython → always available.
    assert _bare_microsd().is_inserted is True


def test_is_inserted_tracks_mount_on_micropython(monkeypatch):
    # On ESP32 is_inserted must reflect a real mount, so save() won't write to an
    # absent card. No card on the test host → not inserted.
    monkeypatch.setattr(microsd_mod, "IS_MICROPYTHON", True)
    monkeypatch.setattr(SettingsConstants, "MICROSD_MOUNT", "/definitely-not-a-mount/sd")
    assert _bare_microsd().is_inserted is False
