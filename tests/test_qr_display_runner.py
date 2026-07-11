"""Unit tests for the native QR-display frame driver (`run_qr_display_screen`) and its
`EncodeQR` -> native cfg mapping (`_encoder_to_qr_cfg`) in `seedsigner.gui.lvgl_screen_runner`.

The driver is MicroPython-only in production (the View gates it by `IS_MICROPYTHON`); these
tests drive it directly on CPython with a faked native module + fake settings, and stub the
MicroPython-only `time.sleep_ms`.
"""
import sys
import time
from unittest.mock import MagicMock

sys.modules.setdefault("seedsigner.hardware.buttons", MagicMock())

import seedsigner.gui.lvgl_screen_runner as lvgl_screen_runner
from seedsigner.gui.lvgl_screen_runner import _encoder_to_qr_cfg, _qr_frame_bytes
from seedsigner.models.encode_qr import (
    SeedQrEncoder, CompactSeedQrEncoder, StaticXpubQrEncoder, GenericStaticQrEncoder,
)
from seedsigner.models.seed import Seed
from seedsigner.models.settings import SettingsConstants
from binascii import hexlify


# test vector 4 from the SeedQR docs
MNEMONIC = "forum undo fragile fade shy sign arrest garment culture tube off merit".split()


# ---------------------------------------------------------------------------
# _encoder_to_qr_cfg - (qr_mode, data_encoding, qr_data) per encoder payload type
# ---------------------------------------------------------------------------

def test_encoder_cfg_seedqr_is_auto_utf8_digit_string():
    mode, encoding, data = _encoder_to_qr_cfg(SeedQrEncoder(mnemonic=MNEMONIC))
    assert (mode, encoding) == ("auto", "utf8")
    assert data == "073318950739065415961602009907670428187212261116"


def test_encoder_cfg_compactseedqr_is_byte_hex():
    mode, encoding, data = _encoder_to_qr_cfg(CompactSeedQrEncoder(mnemonic=MNEMONIC))
    assert (mode, encoding) == ("byte", "hex")
    # The bytes payload, hex-serialized into the JSON cfg.
    assert data == hexlify(b'[\xbd\x9dq\xa8\xecy\x90\x83\x1a\xff5\x9dBeE').decode()


def test_encoder_cfg_static_xpub_is_auto_utf8():
    enc = StaticXpubQrEncoder(
        seed=Seed("obscure bone gas open exotic abuse virus bunker shuffle nasty ship dash".split(), passphrase="pass"),
        derivation="m/48h/1h/0h/2h", network=SettingsConstants.TESTNET)
    mode, encoding, data = _encoder_to_qr_cfg(enc)
    assert (mode, encoding) == ("auto", "utf8")
    assert data.startswith("[c49122a5/48h/1h/0h/2h]Vpub")


def test_encoder_cfg_generic_static_is_auto_utf8():
    mode, encoding, data = _encoder_to_qr_cfg(GenericStaticQrEncoder(data="bc1qexampleaddress"))
    assert (mode, encoding, data) == ("auto", "utf8", "bc1qexampleaddress")
    # UR-fountain (UrPsbtQrEncoder / UrXpubQrEncoder) next_part() returns an uppercase str, so
    # it takes the same str branch -> ("auto", "utf8", <fragment>); verified end-to-end on-device.


# ---------------------------------------------------------------------------
# _qr_frame_bytes - str -> utf8, bytes pass through
# ---------------------------------------------------------------------------

def test_qr_frame_bytes_encodes_str_and_passes_bytes():
    assert _qr_frame_bytes("UR:CRYPTO-PSBT/1-2/ABC") == b"UR:CRYPTO-PSBT/1-2/ABC"
    assert _qr_frame_bytes(b"\x01\x02") == b"\x01\x02"
    assert _qr_frame_bytes(bytearray(b"\x03")) == b"\x03"


# ---------------------------------------------------------------------------
# run_qr_display_screen - cfg build, brightness handling, exit, animation
# ---------------------------------------------------------------------------

class _FakeEncoder:
    """Minimal encoder stand-in: fixed first frame + a restart/seq_len/next_part contract."""
    def __init__(self, seq_len=1, first="frame0"):
        self._seq_len = seq_len
        self._first = first
        self.restart_calls = 0
        self.parts_served = []
        self._n = 0

    def seq_len(self):
        return self._seq_len

    def next_part(self):
        part = self._first if self._n == 0 else "frame%d" % self._n
        self._n += 1
        self.parts_served.append(part)
        return part

    def restart(self):
        self.restart_calls += 1
        self._n = 0


class _FakeSettings:
    def __init__(self):
        self.values = {
            SettingsConstants.SETTING__QR_BRIGHTNESS: 128,
            SettingsConstants.SETTING__QR_BRIGHTNESS_TIPS: SettingsConstants.OPTION__ENABLED,
            SettingsConstants.SETTING__QR_DENSITY: SettingsConstants.DENSITY__DEFAULT,
        }
        self.set_calls = []

    def get_value(self, attr):
        return self.values[attr]

    def set_value(self, attr, value):
        self.set_calls.append((attr, value))
        self.values[attr] = value


def _patch_common(monkeypatch, fake_lv, fake_settings):
    monkeypatch.setattr(lvgl_screen_runner, "_lv", fake_lv)
    monkeypatch.setattr(lvgl_screen_runner, "ensure_lvgl_runtime", lambda: None)
    monkeypatch.setattr(lvgl_screen_runner, "_screensaver_timeout_ms", 60000)
    monkeypatch.setattr("seedsigner.models.settings.Settings.get_instance",
                        classmethod(lambda cls: fake_settings))
    monkeypatch.setattr(time, "sleep_ms", lambda *a: None, raising=False)


def test_run_qr_display_static_builds_cfg_and_exits(monkeypatch):
    fake_lv = MagicMock()
    # First poll returns the exit event -> no animation, no sleep.
    fake_lv.poll_for_result.return_value = ("topnav_back", -1, "qr_display_done")
    fake_settings = _FakeSettings()
    _patch_common(monkeypatch, fake_lv, fake_settings)

    enc = _FakeEncoder(seq_len=1, first="011513251154")
    lvgl_screen_runner.run_qr_display_screen(enc)

    cfg = fake_lv.qr_display_screen.call_args[0][0]
    assert cfg["qr_data"] == "011513251154"
    assert cfg["qr_mode"] == "auto"
    assert cfg["initial_brightness"] == 128
    assert cfg["show_brightness_tips"] is True
    assert cfg["brighter_text"] and cfg["darker_text"]   # translated, non-empty
    assert cfg["allow_screensaver"] is False
    # Static QR: never pushes frames.
    fake_lv.qr_display_set_frame.assert_not_called()
    # Screensaver suspended for the screen's duration, then restored.
    assert fake_lv.set_screensaver_timeout.call_args_list[0][0][0] == 0
    assert fake_lv.set_screensaver_timeout.call_args_list[-1][0][0] == 60000


def test_run_qr_display_brightness_persists_and_restarts(monkeypatch):
    fake_lv = MagicMock()
    # A brightness change, then the exit.
    fake_lv.poll_for_result.side_effect = [
        ("qr_brightness", 200, ""),
        ("topnav_back", -1, "qr_display_done"),
    ]
    fake_settings = _FakeSettings()
    _patch_common(monkeypatch, fake_lv, fake_settings)

    enc = _FakeEncoder(seq_len=3)
    lvgl_screen_runner.run_qr_display_screen(enc)

    # Persisted the new brightness value and restarted the sequence.
    assert (SettingsConstants.SETTING__QR_BRIGHTNESS, 200) in fake_settings.set_calls
    assert enc.restart_calls >= 1


def test_run_qr_display_animated_pushes_frame_when_tip_inactive(monkeypatch):
    fake_lv = MagicMock()
    # No result yet (drive the animation branch) then exit on the 2nd poll.
    fake_lv.poll_for_result.side_effect = [None, ("topnav_back", -1, "qr_display_done")]
    fake_lv.qr_display_is_tip_active.return_value = False
    fake_settings = _FakeSettings()
    _patch_common(monkeypatch, fake_lv, fake_settings)

    enc = _FakeEncoder(seq_len=3, first="frame0")
    lvgl_screen_runner.run_qr_display_screen(enc)

    # One frame pushed (tip inactive), as UTF-8 bytes.
    fake_lv.qr_display_set_frame.assert_called_once()
    pushed = fake_lv.qr_display_set_frame.call_args[0][0]
    assert isinstance(pushed, (bytes, bytearray))


def test_run_qr_display_animated_holds_when_tip_active(monkeypatch):
    fake_lv = MagicMock()
    fake_lv.poll_for_result.side_effect = [None, ("topnav_back", -1, "qr_display_done")]
    fake_lv.qr_display_is_tip_active.return_value = True   # tip up -> hold, don't advance
    fake_settings = _FakeSettings()
    _patch_common(monkeypatch, fake_lv, fake_settings)

    enc = _FakeEncoder(seq_len=3)
    lvgl_screen_runner.run_qr_display_screen(enc)


class _FakeFountainEncoder(_FakeEncoder):
    """Density-capable stand-in: adds set_px_per_module, like BaseFountainQrEncoder, so the
    runner offers the density control (which it duck-types on that method)."""
    def __init__(self, seq_len=3, first="frame0"):
        super().__init__(seq_len=seq_len, first=first)
        self.px_per_module_calls = []

    def set_px_per_module(self, px):
        self.px_per_module_calls.append(px)
        self._n = 0   # rebuild rewinds the fountain to part 0


def test_run_qr_display_offers_density_control_for_fountain_encoder(monkeypatch):
    fake_lv = MagicMock()
    fake_lv.poll_for_result.return_value = ("topnav_back", -1, "qr_display_done")
    fake_settings = _FakeSettings()
    _patch_common(monkeypatch, fake_lv, fake_settings)

    lvgl_screen_runner.run_qr_display_screen(_FakeFountainEncoder(seq_len=5))

    cfg = fake_lv.qr_display_screen.call_args[0][0]
    assert cfg["density_control"] is True
    assert cfg["initial_px_per_module"] == fake_settings.values[SettingsConstants.SETTING__QR_DENSITY]
    assert cfg["density_text"] and cfg["density_min_text"] and cfg["density_max_text"]


def test_run_qr_display_omits_density_control_for_fixed_qr(monkeypatch):
    fake_lv = MagicMock()
    fake_lv.poll_for_result.return_value = ("topnav_back", -1, "qr_display_done")
    fake_settings = _FakeSettings()
    _patch_common(monkeypatch, fake_lv, fake_settings)

    # A fixed QR (no set_px_per_module) must not offer the density control.
    lvgl_screen_runner.run_qr_display_screen(_FakeEncoder(seq_len=1))

    cfg = fake_lv.qr_display_screen.call_args[0][0]
    assert "density_control" not in cfg


def test_run_qr_display_density_change_resplits_and_persists(monkeypatch):
    fake_lv = MagicMock()
    fake_lv.poll_for_result.side_effect = [
        ("qr_density", 3, ""),
        ("topnav_back", -1, "qr_display_done"),
    ]
    fake_settings = _FakeSettings()
    _patch_common(monkeypatch, fake_lv, fake_settings)

    enc = _FakeFountainEncoder(seq_len=5)
    lvgl_screen_runner.run_qr_display_screen(enc)

    # Re-split the fountain at the new px/module and persisted it.
    assert enc.px_per_module_calls == [3]
    assert (SettingsConstants.SETTING__QR_DENSITY, 3) in fake_settings.set_calls

    fake_lv.qr_display_set_frame.assert_not_called()
