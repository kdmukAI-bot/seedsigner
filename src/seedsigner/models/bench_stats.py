"""Camera-pipeline benchmark counters for the v0.8.7 baseline arm.

This is the v0.8.7 side of the QR-scan / camera-FPS / entropy-preview benchmark. It
mirrors, metric for metric, the native counters the dev pipeline exposes through
`camera_scanner._debug_stats()` / `_debug_decode_stats()` / `_debug_decode_timing()` and
`camera_entropy._debug_display_frames()`, so the two builds can be compared at the same
measurement boundaries:

    dev (native)                          v0.8.7 (here)
    ------------------------------------  ------------------------------------
    s_n_in    (libcamera delivery)        capture_frames   (PiVideoStream.update)
    s_n_pub   (blit to panel)             display_frames   (renderer.show_image)
    s_n_dec / s_n_hit (zbar passes)       attempts / hits  (pyzbar passes)
    ok / no buckets around zbar_scan_image  decode_ok / decode_no
    stride-strip to contiguous Y          prep (pyzbar._pixel_data)

Timing buckets carry count / sum / sum-of-squares / min / max rather than samples, so a
long run costs no memory and still yields mean + stddev. Sum-of-squares accumulates in
us^2 (not ns^2) for the same reason the native side does it: ns^2 overflows.

THREADING: the three writers touch disjoint fields -- the camera thread only bumps
capture_frames, the live-preview thread only display_frames, the decode loop only the
decode counters and timing buckets -- so no lock is needed. Each individual field has a
single writer.
"""
import time


class TimingBucket:
    """One outcome bucket of a timed segment: count, sum, sum-of-squares, min, max."""

    def __init__(self):
        self.n = 0
        self.sum_ns = 0
        self.sumsq_us2 = 0
        self.min_ns = 0
        self.max_ns = 0


    def add(self, ns: int):
        self.n += 1
        self.sum_ns += ns
        us = ns / 1000.0
        self.sumsq_us2 += us * us
        if self.n == 1:
            self.min_ns = ns
            self.max_ns = ns
        else:
            if ns < self.min_ns:
                self.min_ns = ns
            if ns > self.max_ns:
                self.max_ns = ns


    @property
    def mean_us(self) -> float:
        return (self.sum_ns / self.n / 1000.0) if self.n else 0.0


    @property
    def stddev_us(self) -> float:
        """Population stddev via E[x^2] - E[x]^2, clamped at 0 (a very tight bucket can
        land marginally negative on floating-point cancellation)."""
        if self.n == 0:
            return 0.0
        mean = self.mean_us
        var = self.sumsq_us2 / self.n - mean * mean
        return var ** 0.5 if var > 0.0 else 0.0


    def summary(self) -> dict:
        return {
            "n": self.n,
            "mean_us": round(self.mean_us, 1),
            "stddev_us": round(self.stddev_us, 1),
            "min_us": round(self.min_ns / 1000.0, 1),
            "max_us": round(self.max_ns / 1000.0, 1),
        }



class BenchStats:
    """Process-wide benchmark counters. Read at t0/t1 over a fixed window."""

    def __init__(self):
        self.reset()


    def reset(self):
        # Frame flow
        self.capture_frames = 0          # frames PiVideoStream published (capture ceiling)
        self.display_frames = 0          # scan live-preview renders (display FPS)
        self.entropy_display_frames = 0  # entropy live-preview renders (display FPS)

        # Decode outcome counts. The `_fresh` pair only counts passes that ran on a
        # capture the decode loop had not already seen. v0.8.7's decode loop is not
        # frame-gated -- it re-runs on the same latest-frame slot between camera
        # deliveries -- so the raw pair over-counts relative to the dev pipeline's
        # new-frame-gated worker. Both pairs are recorded; neither gates the decode.
        self.attempts = 0
        self.hits = 0
        self.attempts_fresh = 0
        self.hits_fresh = 0

        # Timed segments
        self.decode_ok = TimingBucket()   # pyzbar pass that returned >=1 symbol
        self.decode_no = TimingBucket()   # pyzbar pass that returned nothing
        self.prep = TimingBucket()        # pyzbar._pixel_data (frame -> 8bpp buffer)

        # How many times the decoder reassembled a complete payload during the window.
        self.completions = 0

        # Set by the decode loop before each pass so record_decode() can bucket by
        # freshness. Single decode thread, so a plain attribute is sufficient.
        self._pass_is_fresh = False

        self.t0 = time.monotonic()


    def begin_pass(self, is_fresh: bool):
        self._pass_is_fresh = is_fresh


    def record_decode(self, prep_ns: int, decode_ns: int, hit: bool):
        self.prep.add(prep_ns)
        self.attempts += 1
        if hit:
            self.hits += 1
            self.decode_ok.add(decode_ns)
        else:
            self.decode_no.add(decode_ns)

        if self._pass_is_fresh:
            self.attempts_fresh += 1
            if hit:
                self.hits_fresh += 1


    def elapsed(self) -> float:
        return max(time.monotonic() - self.t0, 1e-6)


    def focus_lines(self) -> list:
        """A compact readout to draw over the live preview while aiming and focusing.

        Deliberately terse: it shares the panel with the picture being aimed. The caller
        resets between reports, so these are the numbers for the last short interval, not
        for the session -- a cumulative average would barely move when the lens is
        corrected, which is exactly the feedback this is for.
        """
        dt = self.elapsed()
        ratio = (self.hits / self.attempts) if self.attempts else 0.0
        ok = self.decode_ok
        return [
            f"disp{self.display_frames / dt:5.1f} cap{self.capture_frames / dt:5.1f}",
            f"att{self.attempts / dt:5.1f} hit{self.hits / dt:5.1f} r{ratio:4.2f}",
            f"ok{ok.mean_us / 1000.0:7.1f}+-{ok.stddev_us / 1000.0:.1f}ms n{ok.n}",
        ]


    def snapshot(self) -> dict:
        dt = self.elapsed()
        return {
            "window_s": round(dt, 2),
            "capture_fps": round(self.capture_frames / dt, 2),
            "display_fps": round(self.display_frames / dt, 2),
            "entropy_display_fps": round(self.entropy_display_frames / dt, 2),
            "attempts": self.attempts,
            "hits": self.hits,
            "attempts_per_s": round(self.attempts / dt, 2),
            "hits_per_s": round(self.hits / dt, 2),
            "ratio": round(self.hits / self.attempts, 3) if self.attempts else 0.0,
            "attempts_fresh": self.attempts_fresh,
            "hits_fresh": self.hits_fresh,
            "hits_fresh_per_s": round(self.hits_fresh / dt, 2),
            "ratio_fresh": (round(self.hits_fresh / self.attempts_fresh, 3)
                            if self.attempts_fresh else 0.0),
            "completions": self.completions,
            "decode_ok": self.decode_ok.summary(),
            "decode_no": self.decode_no.summary(),
            "prep": self.prep.summary(),
        }



# The one and only instance; imported directly by the instrumented call sites.
BENCH = BenchStats()
