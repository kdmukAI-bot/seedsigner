#!/usr/bin/env python
"""Camera-pipeline benchmark harness for the v0.8.7 baseline image.

This is what the benchmark image boots into instead of the SeedSigner app. It drives the
real v0.8.7 scan and entropy-preview screens -- same camera settings, same three-thread
structure, same preview rendering -- for a fixed measurement window, then reports the
counters `seedsigner.models.bench_stats` collected and appends the run to the microSD
card.

WHY IT REPLACES THE APP RATHER THAN ADDING A MENU ENTRY: the release image has no
console and no shell, and the app's scan flow ends as soon as a payload finishes
reassembling, so it cannot hold a controlled window or show a readout. Booting straight
into the harness makes every run identical and needs no navigation.

CONTROLS (the display is the only output; there is no console on a release image)

    RIGHT       focus mode: unbounded, live readout over the preview, saves nothing
    KEY1        run a scan benchmark for the selected duration
    KEY2        run an entropy live-preview benchmark
    KEY3        cycle this device's display configuration (240x240 <-> 320x240)
    UP / DOWN   change the measurement window (15 / 30 / 60 s)
    PRESS       show what this card already holds
    LEFT/RIGHT  during a run: leave it

Do a focus pass before every measured run. Focus is a per-camera confound that moves the
success ratio and the hit rate while leaving the decode time alone, so an unfocused board
looks slower in exactly the numbers that are supposed to be about the board.

ONE CARD, SEVERAL BOARDS: every record carries the Pi's own serial number, board model
and display configuration, and the display configuration is stored per serial -- so the
same card can be moved between the Pi Zero, the Zero 2 W and the 320x240 SeedSigner+
without either losing earlier runs or carrying one board's panel setting onto another.
Runs are ordered by `run_seq`, a card-wide counter, because a Pi has no RTC.
"""
import sys
import traceback

from seedsigner.models import bench_storage
from seedsigner.models.bench_stats import BENCH
from seedsigner.models.settings import Settings
from seedsigner.models.settings_definition import SettingsConstants, SettingsDefinition


DURATIONS = [15, 30, 60]

DISPLAY_CONFIGS = [
    SettingsConstants.DISPLAY_CONFIGURATION__ST7789__240x240,
    SettingsConstants.DISPLAY_CONFIGURATION__ST7789__320x240,
]

# Stamped into every record so runs from different builds of this image never get pooled.
IMAGE_BUILD = "v0.8.7-bench"
APP_VERSION = "0.8.7"



# S02seedsigner backgrounds this process before S10mdev starts the daemon that mounts the
# card, and the coldplug pass in that init script is commented out -- so the mount arrives
# only when the MMC probe emits its uevent, which is after this process is already running.
CARD_WAIT_S = 20.0


class BenchHarness:
    def __init__(self):
        self.device = bench_storage.read_device_identity()
        self.serial = self.device["device_serial"]
        self.card_ok = False
        self.display_config = DISPLAY_CONFIGS[0]
        self.duration = 30
        self.last_summary = None

        # Defaults, not whatever settings.json a previous life of this card left behind:
        # every board has to run the same configuration for the numbers to be comparable,
        # and a stale camera rotation or panel size would change them silently. The one
        # per-device setting the harness honours is its own display config, applied below.
        self.settings = Settings.get_instance()
        self.settings._data = SettingsDefinition.get_defaults()
        self.settings._data[SettingsConstants.SETTING__DISPLAY_CONFIGURATION] = self.display_config

        # Bring the panel up on the default 240x240 first so there is something to look at
        # while the card is still arriving; the real configuration is applied once it does.
        from seedsigner.gui.renderer import Renderer
        Renderer.configure_instance()
        self.renderer = Renderer.get_instance()

        from seedsigner.hardware.buttons import HardwareButtons
        self.hw_inputs = HardwareButtons.get_instance()

        self.attach_card()


    def attach_card(self):
        """Wait for the card, then adopt this board's stored configuration.

        Falls through after the timeout rather than failing: the harness still runs, the
        menu shows the card as unavailable, and `refresh_card()` picks it up if it turns
        up later -- a run is only ever lost if the card is genuinely not writable.
        """
        import os
        import time

        self.draw_lines(["SeedSigner 0.8.7 BENCH", "", f"dev {self.device['device_name']}",
                         "", "waiting for microSD..."])

        deadline = time.monotonic() + CARD_WAIT_S
        while time.monotonic() < deadline and not os.path.ismount(bench_storage.CARD_MOUNT):
            time.sleep(0.25)

        self.card_ok = bench_storage.ensure_bench_dir()
        if not self.card_ok:
            return

        config = bench_storage.load_device_config(self.serial)

        duration = config.get("duration_s", 30)
        self.duration = duration if duration in DURATIONS else 30

        stored = config.get("display_config")
        if stored in DISPLAY_CONFIGS and stored != self.display_config:
            self.display_config = stored
            self.settings._data[SettingsConstants.SETTING__DISPLAY_CONFIGURATION] = stored
            self.renderer.initialize_display()


    def refresh_card(self) -> bool:
        """Re-test writability rather than trusting the answer from start-up."""
        self.card_ok = bench_storage.ensure_bench_dir()
        return self.card_ok


    # ------------------------------------------------------------------ persistence

    def save_device_config(self):
        if not self.card_ok:
            return
        bench_storage.save_device_config(self.serial, {
            "display_config": self.display_config,
            "duration_s": self.duration,
            "device_name": self.device["device_name"],
            "device_model": self.device["device_model"],
        })


    # ------------------------------------------------------------------ drawing

    def _font(self, size: int):
        from seedsigner.gui.components import Fonts, GUIConstants
        return Fonts.get_font(GUIConstants.FIXED_WIDTH_FONT_NAME, size)


    def draw_lines(self, lines: list, size: int = 13):
        """Render a block of monospaced lines. Long runs of numbers are what this image
        exists to show, so everything is drawn as plain left-aligned text."""
        font = self._font(size)
        with self.renderer.lock:
            self.renderer.draw.rectangle(
                (0, 0, self.renderer.canvas_width, self.renderer.canvas_height),
                fill="black",
            )
            y = 4
            line_height = size + 3
            for line in lines:
                if y + line_height > self.renderer.canvas_height:
                    break
                self.renderer.draw.text((4, y), line, fill="#FCFCFC", font=font)
                y += line_height
            self.renderer.show_image()


    def wait_for_release(self):
        """Block until no button is held, so one press is not read by two screens."""
        import time
        while self.hw_inputs.has_any_input():
            time.sleep(0.02)
        time.sleep(0.05)


    # ------------------------------------------------------------------ screens

    def show_menu(self):
        self.refresh_card()
        runs = bench_storage.count_device_runs(self.serial) if self.card_ok else 0
        total = (bench_storage.next_run_seq() - 1) if self.card_ok else 0
        panel = self.display_config.replace("st7789_", "")

        self.draw_lines([
            "SeedSigner 0.8.7 BENCH",
            "",
            f"dev  {self.device['device_name']}",
            f"     {self.device['device_model'][:26]}",
            f"disp {panel}",
            f"win  {self.duration}s",
            f"card {'OK' if self.card_ok else 'NOT WRITABLE'}",
            f"runs {runs} here / {total} card",
            "",
            "RIGHT focus (no save)",
            "K1 scan     K2 entropy",
            "K3 display  UP/DN window",
            "PRESS  card contents",
        ])


    def show_card_contents(self):
        if not self.card_ok:
            self.draw_lines(["CARD NOT WRITABLE", "", "/mnt/microsd is not",
                             "mounted or is read-only.", "", "any key to go back"])
            return

        lines = [f"CARD: {bench_storage.next_run_seq() - 1} runs", ""]
        try:
            import json
            with open(bench_storage.RUNS_JSONL) as f:
                records = [json.loads(line) for line in f if line.strip()]
            for record in records[-8:]:
                lines.append(
                    f"#{record.get('run_seq')} {record.get('device_name', '?')[:12]}"
                )
                lines.append(
                    f"  {record.get('mode', '?')[:4]} {record.get('display_config', '?').replace('st7789_', '')}"
                    f" ok={record.get('decode_ok', {}).get('mean_us', 0) / 1000.0:.0f}ms"
                )
        except (OSError, ValueError):
            lines.append("(no runs yet)")

        lines += ["", "any key to go back"]
        self.draw_lines(lines, size=12)


    def cycle_display_config(self):
        index = (DISPLAY_CONFIGS.index(self.display_config) + 1) % len(DISPLAY_CONFIGS)
        self.display_config = DISPLAY_CONFIGS[index]
        self.settings._data[SettingsConstants.SETTING__DISPLAY_CONFIGURATION] = self.display_config

        # Rebuilds the driver and the canvas at the new size in place.
        self.renderer.initialize_display()
        self.save_device_config()


    def cycle_duration(self, step: int):
        index = (DURATIONS.index(self.duration) + step) % len(DURATIONS)
        self.duration = DURATIONS[index]
        self.save_device_config()


    # ------------------------------------------------------------------ runs

    def run_scan(self):
        from seedsigner.gui.screens.scan_screens import ScanScreen
        from seedsigner.models.decode_qr import DecodeQR

        self.draw_lines([f"SCAN {self.duration}s", "", "aim at an animated QR",
                         "starting..."])

        BENCH.reset()
        screen = ScanScreen(
            decoder=DecodeQR(),
            instructions_text="bench",
            bench_seconds=self.duration,
        )
        screen.display()
        return self.record_run("scan")


    def run_focus(self):
        """Unbounded aim/focus mode. Reports the same numbers as a measured scan, live
        over the preview, and records nothing -- it exists to get the camera right first,
        because focus moves the success ratio and the hit rate without moving the decode
        time, which reads like a slower board if it is not controlled."""
        from seedsigner.gui.screens.scan_screens import ScanScreen
        from seedsigner.models.decode_qr import DecodeQR

        self.draw_lines(["FOCUS MODE", "", "unbounded, nothing saved",
                         "adjust aim + focus until",
                         "ratio ~1.0 and hit/s peaks",
                         "", "LEFT or RIGHT to exit"])
        self.wait_for_any()

        BENCH.reset()
        screen = ScanScreen(
            decoder=DecodeQR(),
            instructions_text="focus",
            bench_focus=True,
        )
        screen.display()


    def run_entropy(self):
        from seedsigner.gui.screens.tools_screens import ToolsImageEntropyLivePreviewScreen

        self.draw_lines([f"ENTROPY {self.duration}s", "", "point at anything",
                         "starting..."])

        BENCH.reset()
        screen = ToolsImageEntropyLivePreviewScreen(bench_seconds=self.duration)
        screen.display()
        return self.record_run("entropy")


    def record_run(self, mode: str) -> dict:
        from seedsigner.gui.screens.scan_screens import ScanScreen

        # The card can arrive after start-up, so ask again rather than assume the answer
        # from boot still holds.
        self.refresh_card()

        snapshot = BENCH.snapshot()

        if mode == "scan":
            scan_resolution = "x".join(str(v) for v in ScanScreen.resolution)
            scan_framerate = ScanScreen.framerate
            display_fps = snapshot["display_fps"]
        else:
            # The entropy preview asks the camera for a square the size of the panel's
            # longest edge, so the resolution is a function of the display configuration.
            longest = max(self.renderer.canvas_width, self.renderer.canvas_height)
            scan_resolution = f"{longest}x{longest}"
            scan_framerate = 24
            display_fps = snapshot["entropy_display_fps"]

        record = {
            "run_seq": bench_storage.next_run_seq() if self.card_ok else 0,
            "run_on_device": (bench_storage.count_device_runs(self.serial) + 1) if self.card_ok else 0,
            "mode": mode,
            "display_config": self.display_config,
            "scan_resolution": scan_resolution,
            "scan_framerate": scan_framerate,
            "duration_s": self.duration,
            "app_version": APP_VERSION,
            "image_build": IMAGE_BUILD,
            "uptime_s": bench_storage.uptime_s(),
            "wallclock_untrusted": bench_storage.wallclock_untrusted(),
        }
        record.update(self.device)
        record.update(snapshot)
        record["display_fps"] = display_fps

        record["saved"] = bench_storage.append_run(record) if self.card_ok else False
        self.last_summary = record
        return record


    def show_results(self, record: dict):
        ok = record["decode_ok"]
        panel = record["display_config"].replace("st7789_", "")

        if record["mode"] == "scan":
            lines = [
                f"#{record['run_seq']} {record['device_name']}",
                f"scan {record['duration_s']}s {record['scan_resolution']}"
                f"@{record['scan_framerate']} {panel}",
                "-" * 24,
                f"window {record['window_s']:.1f}s",
                f"disp {record['display_fps']:5.1f}  cap {record['capture_fps']:5.1f}",
                f"att/s {record['attempts_per_s']:5.1f} hit/s {record['hits_per_s']:5.1f}",
                f"ratio {record['ratio']:.2f}",
                f"fresh hit/s {record['hits_fresh_per_s']:5.1f}",
                "-" * 24,
                f"ok {ok['mean_us'] / 1000.0:.1f}ms +-{ok['stddev_us'] / 1000.0:.1f}",
                f"  [{ok['min_us'] / 1000.0:.1f}..{ok['max_us'] / 1000.0:.1f}] n={ok['n']}",
                f"prep {record['prep']['mean_us'] / 1000.0:.2f}ms",
                f"no-data n={record['decode_no']['n']}"
                f" {record['decode_no']['mean_us'] / 1000.0:.1f}ms",
                "-" * 24,
                "SAVED to card" if record["saved"] else "NOT SAVED",
                "any key to continue",
            ]
            if ok["n"] == 0:
                lines.insert(3, "!! 0 decodes: aim/focus")
        else:
            lines = [
                f"#{record['run_seq']} {record['device_name']}",
                f"entropy {record['duration_s']}s {panel}",
                "-" * 24,
                f"window {record['window_s']:.1f}s",
                f"disp {record['display_fps']:5.1f}",
                f"cap  {record['capture_fps']:5.1f}",
                f"preview {record['scan_resolution']}",
                "-" * 24,
                "SAVED to card" if record["saved"] else "NOT SAVED",
                "any key to continue",
            ]

        self.draw_lines(lines, size=12)
        print("\n".join(lines), file=sys.stderr)


    # ------------------------------------------------------------------ main loop

    def loop(self):
        import time
        from seedsigner.hardware.buttons import HardwareButtonsConstants as K

        while True:
            self.show_menu()
            self.wait_for_release()

            while True:
                if self.hw_inputs.check_for_low(K.KEY1):
                    self.wait_for_release()
                    self.show_results(self.run_scan())
                    self.wait_for_any()
                    break
                if self.hw_inputs.check_for_low(K.KEY2):
                    self.wait_for_release()
                    self.show_results(self.run_entropy())
                    self.wait_for_any()
                    break
                if self.hw_inputs.check_for_low(K.KEY3):
                    self.wait_for_release()
                    self.cycle_display_config()
                    break
                if self.hw_inputs.check_for_low(K.KEY_RIGHT):
                    self.wait_for_release()
                    self.run_focus()
                    break
                if self.hw_inputs.check_for_low(K.KEY_UP):
                    self.wait_for_release()
                    self.cycle_duration(1)
                    break
                if self.hw_inputs.check_for_low(K.KEY_DOWN):
                    self.wait_for_release()
                    self.cycle_duration(-1)
                    break
                if self.hw_inputs.check_for_low(K.KEY_PRESS):
                    self.wait_for_release()
                    self.show_card_contents()
                    self.wait_for_any()
                    break
                time.sleep(0.02)


    def wait_for_any(self):
        import time
        self.wait_for_release()
        while not self.hw_inputs.has_any_input():
            time.sleep(0.02)
        self.wait_for_release()



def main():
    import logging
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

    harness = None
    try:
        harness = BenchHarness()
        harness.loop()
    except Exception:
        detail = traceback.format_exc()
        print(detail, file=sys.stderr)
        bench_storage.append_error(detail)

        # A release image has no console, so the panel is the only place a crash can be
        # reported. If the display never came up there is nothing more to do.
        if harness is not None:
            try:
                harness.draw_lines(["CRASH"] + detail.strip().splitlines()[-9:], size=11)
            except Exception:
                pass
        raise


if __name__ == "__main__":
    main()
