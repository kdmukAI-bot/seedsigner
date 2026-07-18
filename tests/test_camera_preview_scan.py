"""Unit tests for the Pi Zero LVGL camera-preview scan (run_camera_preview_scan) and its
numpy->RGB565 conversion.

Off-device: the native runtime, renderer, picamera, and hardware buttons are mocked. Under test:
(1) the numpy->RGB565 conversion, (2) the _CameraScanDecodeThread decode worker (driven
synchronously for determinism), and (3) run_camera_preview_scan's OUTCOMES (build/complete/cancel/
teardown). The runner is now two-threaded (a background decode worker + a preview loop), so exact
per-frame call counts are nondeterministic; the tests assert observable outcomes, not cadence. Real
decode + visual preview are validated on-device.
"""
import sys
import time
import types
from unittest.mock import MagicMock

import pytest

import seedsigner.gui.lvgl_screen_runner as runner
from seedsigner.models.decode_qr import DecodeQRStatus


def _real_numpy():
    """Real numpy, or skip. tests/base.py stubs ``sys.modules['numpy']`` with a MagicMock
    (numpy is a Pi-only dependency, absent on dev/CI), so importorskip alone would hand back
    the mock; the conversion tests need the genuine article. They run on-device / anywhere
    numpy is truly installed (e.g. the raspi-lvgl Docker pytest gate)."""
    np = pytest.importorskip("numpy")
    if isinstance(np, MagicMock):
        pytest.skip("numpy is mocked on this platform (Pi-only dependency)")
    return np


# --- numpy -> LVGL-native RGB565 conversion -----------------------------------

def test_numpy_rgb_to_rgb565_size_and_packing():
    """w*h*2 bytes of little-endian RGB565; pure red packs to 0xF800 (the Stage-1
    on-hardware-locked, no-pre-swap contract)."""
    np = _real_numpy()
    frame = np.empty((480, 480, 3), dtype=np.uint8)
    frame[:] = (255, 0, 0)  # pure red everywhere
    out = runner._numpy_rgb_to_rgb565(frame, 0)
    assert len(out) == 240 * 240 * 2
    px = np.frombuffer(out, dtype="<u2")
    assert (px == 0xF800).all()  # RRRRR=0x1f in bits 15..11


def test_numpy_rgb_to_rgb565_channels_and_rotation():
    """Distinct per-corner colors verify both the 5/6/5 packing and the 90-deg-CCW
    rotation (Image.rotate(90 + camera_rotation) parity)."""
    np = _real_numpy()
    frame = np.zeros((480, 480, 3), dtype=np.uint8)
    frame[:240, :240] = (255, 0, 0)      # TL red
    frame[:240, 240:] = (0, 255, 0)      # TR green
    frame[240:, :240] = (0, 0, 255)      # BL blue
    frame[240:, 240:] = (255, 255, 255)  # BR white
    px = np.frombuffer(runner._numpy_rgb_to_rgb565(frame, 0), dtype="<u2").reshape(240, 240)
    # 90 CCW: TR->TL, BR->TR, TL->BL, BL->BR.
    assert px[0, 0] == 0x07E0      # green (GGGGGG=0x3f in bits 10..5)
    assert px[0, 239] == 0xFFFF    # white
    assert px[239, 0] == 0xF800    # red
    assert px[239, 239] == 0x001F  # blue (BBBBB=0x1f in bits 4..0)


def test_numpy_rgb_to_rgb565_rotation_180():
    """camera_rotation=90 -> 180-deg total; a top-left marker lands bottom-right."""
    np = _real_numpy()
    frame = np.zeros((480, 480, 3), dtype=np.uint8)
    frame[:240, :240] = (255, 0, 0)  # TL red only
    px = np.frombuffer(runner._numpy_rgb_to_rgb565(frame, 90), dtype="<u2").reshape(240, 240)
    assert px[0, 0] == 0x0000       # TL now empty
    assert px[239, 239] == 0xF800   # red rotated to BR


# --- _CameraScanDecodeThread (driven synchronously for determinism) -----------

def _decoder(statuses, percents):
    d = MagicMock()
    d.add_image.side_effect = list(statuses)
    d.get_percent_complete.side_effect = list(percents)
    return d


def test_decode_thread_reaches_complete():
    """A miss, a part, then completion: run() returns on COMPLETE with the terminal flags set
    and the monotonic percent latched."""
    camera = MagicMock()
    camera.read_video_stream.return_value = object()
    d = _decoder([DecodeQRStatus.FALSE, DecodeQRStatus.PART_COMPLETE, DecodeQRStatus.COMPLETE],
                 [0, 40, 100])
    t = runner._CameraScanDecodeThread(camera, d)
    t.keep_running = True
    t.run()  # synchronous — returns when COMPLETE reached
    assert t.done is True
    assert t.complete is True
    assert t.status == DecodeQRStatus.COMPLETE
    assert t.percent == 100


def test_decode_thread_invalid_is_terminal_but_not_complete():
    camera = MagicMock()
    camera.read_video_stream.return_value = object()
    d = _decoder([DecodeQRStatus.INVALID], [0])
    t = runner._CameraScanDecodeThread(camera, d)
    t.keep_running = True
    t.run()
    # INVALID ends the scan (done) but is not a successful decode (complete=False); the
    # caller distinguishes COMPLETE vs INVALID off the decoder itself.
    assert t.done is True
    assert t.complete is False
    assert t.status == DecodeQRStatus.INVALID


def test_decode_thread_percent_is_monotonic():
    """A dipping weighted estimate never lowers the reported percent."""
    camera = MagicMock()
    camera.read_video_stream.return_value = object()
    # frame2's 20 is below frame1's 40 -> must clamp to 40; frame3 completes at 100.
    d = _decoder([DecodeQRStatus.PART_COMPLETE, DecodeQRStatus.PART_COMPLETE, DecodeQRStatus.COMPLETE],
                 [40, 20, 100])
    t = runner._CameraScanDecodeThread(camera, d)
    t.keep_running = True
    t.run()
    assert t._max_pct == 100
    assert t.percent == 100  # never regressed below 40 en route


# --- run_camera_preview_scan (outcomes; two-threaded so counts are nondeterministic) --

@pytest.fixture
def scan_env(monkeypatch):
    """Stub the native runtime + renderer + picamera + buttons for run_camera_preview_scan.

    The numpy->RGB565 conversion is patched out (covered by the conversion tests), so the loop
    tests need no real numpy. `make_decoder`'s add_image simulates a short decode and never
    raises on overrun (the background thread may call it many times), so completion is driven by
    the scripted status sequence. time.sleep is capped so the two threads interleave quickly.
    """
    real_sleep = time.sleep
    monkeypatch.setattr("time.sleep", lambda s=0: real_sleep(min(s, 0.005)))

    fake_lv = MagicMock()
    monkeypatch.setattr(runner, "_lv", fake_lv)
    monkeypatch.setattr(runner, "ensure_lvgl_runtime", lambda: None)
    monkeypatch.setattr(runner, "stop_loading_pump", lambda: None)
    monkeypatch.setattr(runner, "_screensaver_timeout_ms", 60000)
    monkeypatch.setattr(runner, "_numpy_rgb_to_rgb565",
                        lambda frame, rotation: b"\x00" * (240 * 240 * 2))

    renderer = MagicMock()
    monkeypatch.setitem(
        sys.modules, "seedsigner.gui.renderer",
        types.SimpleNamespace(Renderer=MagicMock(get_instance=lambda: renderer)))

    class CameraConnectionError(Exception):
        pass

    camera = MagicMock()
    camera._camera_rotation = 0
    camera._video_stream = object()
    camera.read_video_stream.return_value = object()  # opaque non-None captured frame
    monkeypatch.setitem(
        sys.modules, "seedsigner.hardware.camera",
        types.SimpleNamespace(Camera=MagicMock(get_instance=lambda: camera),
                              CameraConnectionError=CameraConnectionError))

    hw = MagicMock()
    hw.check_for_low.return_value = False
    monkeypatch.setitem(
        sys.modules, "seedsigner.hardware.buttons",
        types.SimpleNamespace(
            HardwareButtons=MagicMock(get_instance=lambda: hw),
            HardwareButtonsConstants=types.SimpleNamespace(KEY_LEFT=3, KEY_RIGHT=15)))

    def make_decoder(statuses):
        seq = list(statuses)
        box = {"i": 0}
        decoder = MagicMock()

        def add_image(_img):
            time.sleep(0.002)  # simulate a short decode; tame the background hot-loop
            i = box["i"]
            box["i"] = i + 1
            return seq[i] if i < len(seq) else (seq[-1] if seq else DecodeQRStatus.FALSE)

        decoder.add_image.side_effect = add_image
        decoder.get_percent_complete.side_effect = lambda **k: 40
        return decoder

    return types.SimpleNamespace(lv=fake_lv, hw=hw, camera=camera, renderer=renderer,
                                 make_decoder=make_decoder, CameraConnectionError=CameraConnectionError)


def test_run_camera_preview_scan_completes(scan_env):
    """Background decode reaches COMPLETE: the screen is built with the composed instruction
    line, a frame is pushed, the bar snaps to full, teardown runs, and a complete ScanResult
    is returned."""
    decoder = scan_env.make_decoder(
        [DecodeQRStatus.FALSE, DecodeQRStatus.PART_COMPLETE, DecodeQRStatus.COMPLETE])

    result = runner.run_camera_preview_scan(decoder, instructions_text="< back  |  Scan a QR code")

    lv = scan_env.lv
    lv.camera_preview_screen.assert_called_once()
    assert lv.camera_preview_screen.call_args[0][0]["instructions_text"] == "< back  |  Scan a QR code"
    assert lv.camera_preview_set_frame.called          # preview rendered at least once
    assert (100, 1) in [c.args for c in lv.camera_preview_set_progress.call_args_list]  # completion beat
    lv.camera_preview_close.assert_called_once()
    lv.set_flush_callback.assert_called_with(None)     # dropped on teardown
    scan_env.camera.stop_video_stream_mode.assert_called_once()
    assert decoder.add_image.called                     # decode ran off-thread
    assert result.complete is True
    assert result.cancelled is False


def test_run_camera_preview_scan_cancel_via_hardware_back(scan_env):
    """Joystick LEFT/RIGHT (check_for_low True) cancels; the decode thread is stopped and
    teardown still runs."""
    scan_env.hw.check_for_low.return_value = True
    decoder = scan_env.make_decoder([DecodeQRStatus.FALSE])  # never completes on its own

    result = runner.run_camera_preview_scan(decoder)

    assert result.cancelled is True
    assert result.complete is False
    scan_env.lv.camera_preview_close.assert_called_once()
    scan_env.camera.stop_video_stream_mode.assert_called_once()


def test_run_camera_preview_scan_returns_none_on_camera_failure(scan_env):
    """Camera bring-up failure -> None so ScanView routes to ScanCameraErrorView; the decode
    thread is never started."""
    scan_env.camera.start_video_stream_mode.side_effect = scan_env.CameraConnectionError()
    result = runner.run_camera_preview_scan(scan_env.make_decoder([]))
    assert result is None
    scan_env.lv.camera_preview_screen.assert_not_called()  # never got to build
