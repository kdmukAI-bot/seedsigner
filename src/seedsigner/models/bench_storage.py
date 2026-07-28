"""SD-card result store for the v0.8.7 benchmark image.

The benchmark card is carried from board to board, so every record has to say for itself
which device produced it and under which configuration. Nothing here depends on the run
order of the card's previous life: the store is append-only, it re-derives the next run
number from what is already on the card, and it keys per-device state by the Pi's own
serial number.

WHY NOT TIMESTAMPS: a Pi has no RTC and this image has no network, so `time.time()` is
whatever the kernel starts at and is identical on every run. `run_seq` -- a card-wide
counter derived from the records already stored -- is the orderable field. The wall clock
is recorded anyway, marked untrusted, purely as a tiebreaker within one boot.

LAYOUT on the FAT partition (mounted at /mnt/microsd by mdev, `-o sync`):

    /mnt/microsd/bench/runs.csv        one row per run, spreadsheet-sortable
    /mnt/microsd/bench/runs.jsonl      the same runs, full fidelity, one JSON per line
    /mnt/microsd/bench/dev-<serial>.json   per-device config (display type, run count)
    /mnt/microsd/bench/errors.txt      tracebacks, appended

Every write opens, writes, flushes and fsyncs, then closes. The card can be pulled
between runs without losing the last one.
"""
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

CARD_MOUNT = "/mnt/microsd"
BENCH_DIR = os.path.join(CARD_MOUNT, "bench")

RUNS_CSV = os.path.join(BENCH_DIR, "runs.csv")
RUNS_JSONL = os.path.join(BENCH_DIR, "runs.jsonl")
ERRORS_TXT = os.path.join(BENCH_DIR, "errors.txt")

# Column order for runs.csv. Identity and configuration come first so that sorting the
# sheet by the leftmost columns groups a card's runs by device, then by configuration.
CSV_COLUMNS = [
    "run_seq",
    "device_name",
    "device_serial",
    "device_model",
    "soc",
    "display_config",
    "mode",
    "scan_resolution",
    "scan_framerate",
    "duration_s",
    "run_on_device",
    "capture_fps",
    "display_fps",
    "attempts_per_s",
    "hits_per_s",
    "ratio",
    "hits_fresh_per_s",
    "ratio_fresh",
    "decode_ok_n",
    "decode_ok_mean_us",
    "decode_ok_stddev_us",
    "decode_ok_min_us",
    "decode_ok_max_us",
    "decode_no_n",
    "decode_no_mean_us",
    "prep_n",
    "prep_mean_us",
    "prep_stddev_us",
    "completions",
    "app_version",
    "image_build",
    "uptime_s",
    "wallclock_untrusted",
]



def read_device_identity() -> dict:
    """Board identity straight from the kernel.

    `Serial` in /proc/cpuinfo is burned into the SoC and is what distinguishes two boards
    of the same model on one card. `Revision` encodes the board type; /proc/device-tree/model
    is the human-readable name ("Raspberry Pi Zero 2 W Rev 1.0").
    """
    serial = ""
    revision = ""
    hardware = ""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if key == "Serial":
                    serial = value
                elif key == "Revision":
                    revision = value
                elif key == "Hardware":
                    hardware = value
    except OSError as e:
        logger.warning(f"cpuinfo unreadable: {e}")

    model = ""
    try:
        with open("/proc/device-tree/model") as f:
            model = f.read().strip("\x00").strip()
    except OSError:
        pass

    if not serial:
        # Without a serial there is no stable per-device key; fall back to the revision so
        # records are still grouped, and make the degradation visible in the name.
        serial = f"noserial-{revision or 'unknown'}"

    short = serial[-6:] if len(serial) >= 6 else serial
    slug = "pi"
    lowered = model.lower()
    if "zero 2" in lowered:
        slug = "pi02w"
    elif "zero" in lowered:
        slug = "pi0"
    elif "pi 3" in lowered:
        slug = "pi3"
    elif "pi 2" in lowered:
        slug = "pi2"
    elif "pi 4" in lowered:
        slug = "pi4"

    return {
        "device_serial": serial,
        "device_revision": revision,
        "device_model": model or "unknown",
        "soc": hardware or "unknown",
        "device_name": f"{slug}-{short}",
    }



def _device_config_path(serial: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in serial)
    return os.path.join(BENCH_DIR, f"dev-{safe}.json")



def ensure_bench_dir() -> bool:
    """True only if the bench directory is on the *mounted* card and is writable.

    The mount test is the load-bearing part. Until mdev mounts the card, `/mnt/microsd` is
    an ordinary directory in the RAM rootfs: creating the bench directory under it would
    succeed, accept every write, report success -- and lose everything at power-off. A
    result that silently is not on the card is worse than a result that refuses to save,
    so an unmounted card reads as "not writable".
    """
    if not os.path.ismount(CARD_MOUNT):
        return False
    try:
        os.makedirs(BENCH_DIR, exist_ok=True)
        return os.access(BENCH_DIR, os.W_OK)
    except OSError as e:
        logger.error(f"cannot create {BENCH_DIR}: {e}")
        return False



def load_device_config(serial: str) -> dict:
    """Per-device settings. Keyed by serial so one card carries a different display
    configuration for each board it is moved between -- the 320x240 SeedSigner+ keeps its
    setting without imposing it on the 240x240 boards."""
    path = _device_config_path(serial)
    try:
        with open(path) as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return {}



def save_device_config(serial: str, config: dict) -> None:
    path = _device_config_path(serial)
    try:
        with open(path, "w") as f:
            json.dump(config, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        logger.error(f"cannot write {path}: {e}")



def next_run_seq() -> int:
    """One past the highest run_seq already on the card.

    Counting the JSONL records rather than trusting a stored counter means a card that was
    partly hand-edited, or written by an older build, still produces a strictly increasing
    sequence.
    """
    highest = 0
    try:
        with open(RUNS_JSONL) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    seq = int(json.loads(line).get("run_seq", 0))
                except (ValueError, AttributeError):
                    continue
                if seq > highest:
                    highest = seq
    except OSError:
        pass
    return highest + 1



def count_device_runs(serial: str) -> int:
    """How many runs this particular board has already contributed to the card."""
    n = 0
    try:
        with open(RUNS_JSONL) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    if json.loads(line).get("device_serial") == serial:
                        n += 1
                except ValueError:
                    continue
    except OSError:
        pass
    return n



def _csv_escape(value) -> str:
    s = "" if value is None else str(value)
    if any(c in s for c in ',"\n'):
        return '"' + s.replace('"', '""') + '"'
    return s



def _flat_row(record: dict) -> dict:
    """Flatten the nested timing buckets into the CSV column namespace."""
    flat = dict(record)
    for bucket in ("decode_ok", "decode_no", "prep"):
        for key, value in (record.get(bucket) or {}).items():
            flat[f"{bucket}_{key}"] = value
        flat.pop(bucket, None)
    return flat



def append_run(record: dict) -> bool:
    """Append one run to both stores. Returns False if the card could not be written."""
    if not ensure_bench_dir():
        return False

    ok = True

    try:
        with open(RUNS_JSONL, "a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        logger.error(f"cannot append {RUNS_JSONL}: {e}")
        ok = False

    try:
        need_header = not os.path.exists(RUNS_CSV) or os.path.getsize(RUNS_CSV) == 0
        flat = _flat_row(record)
        with open(RUNS_CSV, "a") as f:
            if need_header:
                f.write(",".join(CSV_COLUMNS) + "\n")
            f.write(",".join(_csv_escape(flat.get(c)) for c in CSV_COLUMNS) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        logger.error(f"cannot append {RUNS_CSV}: {e}")
        ok = False

    return ok



def append_error(text: str) -> None:
    """Best-effort traceback capture. The release image has no console, so a crash that
    is not written here leaves nothing behind."""
    try:
        ensure_bench_dir()
        with open(ERRORS_TXT, "a") as f:
            f.write(text.rstrip() + "\n\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass



def uptime_s() -> float:
    try:
        with open("/proc/uptime") as f:
            return round(float(f.read().split()[0]), 1)
    except (OSError, ValueError, IndexError):
        return 0.0



def wallclock_untrusted() -> str:
    """The kernel's clock. No RTC and no network, so this is not a real date -- it is
    recorded only to separate runs within a single boot."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
