"""`time` millisecond-clock compatibility shim.

The INVERSE of the rest of this package. Every other compat module covers a
CPython API that stock MicroPython lacks; this one covers three MicroPython
APIs that **CPython** lacks:

    time.sleep_ms()   time.ticks_ms()   time.ticks_diff()

They are MicroPython extensions, absent from CPython's `time`. The drive loops
in `gui/lvgl_screen_runner.py` and `hardware/scan_consumer.py` are written once
and run on both interpreters, so they reach the millisecond clock through this
module:

    from seedsigner.compat.time import sleep_ms, ticks_ms, ticks_diff

On MicroPython these ARE the real `time` callables, so the swap is
behaviour-preserving on-device. On CPython they are built from `time.sleep()`
and `time.monotonic()`.

Why this needs a shim rather than an `IS_MICROPYTHON` branch at each call site:
an unguarded `time.sleep_ms(...)` is INVISIBLE on CPython until that exact line
executes, then raises `AttributeError: module 'time' has no attribute
'sleep_ms'` and drops the app into the unhandled-exception view. That is not
hypothetical — it is how image entropy broke on the Pi (2026-07-20), and a
fourth unguarded site sat undetected in seed address verification because
nothing had exercised that flow yet. One import removes the whole class of bug
and keeps the loops a single shape across platforms, which is the design goal
of the native camera contract.

Note this is NOT covered by `tools/mpy_compat_check.py`: every category there
detects CPython-only usage that would fail on MicroPython, so a
MicroPython-only call that fails on CPython is the one direction the checker
does not look. Prefer this module over `time.sleep_ms` anywhere outside an
`IS_MICROPYTHON` branch.
"""

try:
    # MicroPython: the real millisecond clock.
    from time import sleep_ms, ticks_ms, ticks_diff  # noqa: F401

except ImportError:  # CPython (Pi Zero / desktop tests)
    import time as _time

    def sleep_ms(ms):
        """Sleep for `ms` milliseconds."""
        _time.sleep(ms / 1000.0)

    def ticks_ms():
        """Monotonic millisecond counter.

        MicroPython's `ticks_ms` wraps at a platform-dependent maximum and is
        only meaningful through `ticks_diff`; `monotonic()` never wraps, so
        this is strictly the better-behaved of the two. Callers must still go
        through `ticks_diff` to stay correct on-device.
        """
        return int(_time.monotonic() * 1000)

    def ticks_diff(a, b):
        """Signed difference `a - b` between two `ticks_ms()` readings.

        Plain subtraction is correct here because `ticks_ms()` above does not
        wrap. On MicroPython the real `ticks_diff` handles its wraparound.
        """
        return a - b
