"""Native camera-scan drive loop — the Python side of the on-device QR scanner.

This is the native/MicroPython analog of the PIL live-preview loop in
`gui/screens/scan_screens.py`: it drains the native `camera_scanner` NEW ring,
feeds each decoded payload into a `DecodeQR`, reports progress back to the LVGL
preview overlay, and surfaces the sustained-MISS "found but unreadable" warning.
It is the **direct-invoke** consumer — the caller (the LVGL screen runner's scan
entry, driving `ScanView`) calls `camera_scanner.start()`, this loop drives the
decode, and the caller calls `camera_scanner.stop()`.

It is deliberately decoder-agnostic and scanner-injectable: `run_scan` depends
only on the `camera_scanner` contract surface + a duck-typed decoder
(`add_data`, `get_percent_complete`, `is_complete`). That keeps it unit-testable
on CPython with a fake scanner (no device / no native module needed), which is
how the tests exercise it.

User cancel (the overlay's touch back button, or a hardware back/LEFT press) is
surfaced through the injected `should_continue` callable — returning False ends
the loop with a cancelled `ScanResult`, mirroring the Pi Zero KEY_LEFT semantics.

Contract (in seedsigner-micropython-builder): docs/camera-pipeline-phase2-poll-contract.md
  - poll_new()    -> bytes | None      (drain the precious NEW ring to empty)
  - read_status() -> attrtuple(latest, consecutive_misses, dropped_new, has_corners)
  - report(FRAME_*, percent)           (drives cam_present() -> overlay dot/bar)
  - report_complete()                  (terminal, once)

Loop shape (drain-then-read, per the contract §7a "drain to empty each loop"):

    camera_scanner.start()              # caller
    while not done:
        while (p := poll_new()) is not None:      # drain NEW ring fully
            status = decoder.add_data(p)          # classify on THIS (consumer) task
            report(<FRAME_* for status>, pct)     # -> overlay under the LVGL lock
        st = read_status()                        # coalesced latest + counters
        # held-QR / idle dot + sustained-MISS threshold
    camera_scanner.stop()               # caller
"""

# ── DecodeQRStatus values (mirror seedsigner.models.decode_qr.DecodeQRStatus).
# Kept local so this module stays importable without pulling the decode_qr import
# closure (embit + the QR decoders) just for four integer constants; the
# harness/ScanView owns the actual DecodeQR construction.
_PART_COMPLETE = 1
_PART_EXISTING = 2
_COMPLETE      = 3
_FALSE         = 4
_INVALID       = 5

# ── camera_scanner is an ESP32-firmware C module. Import lazily so this file also
# imports on CPython (for tests, where a fake scanner is injected via run_scan).
try:
    import camera_scanner as _default_scanner
except ImportError:  # not on device / not built in
    _default_scanner = None

# ── Timing: MicroPython has time.sleep_ms/ticks_ms/ticks_diff; CPython does not.
# The shim this module used to define inline now lives in compat (the GUI runner
# needs it too), so the loop below runs unchanged on both interpreters.
from seedsigner.compat.time import (
    sleep_ms as _sleep_ms,
    ticks_ms as _ticks_ms,
    ticks_diff as _ticks_diff,
)


class ScanResult:
    """Outcome of a `run_scan` session. `decoder` is the (possibly complete)
    DecodeQR — the caller reads `qr_type` / `get_*` off it for routing."""

    def __init__(self, decoder, complete, cancelled, reason, polls, dropped_new):
        self.decoder = decoder
        self.complete = complete
        self.cancelled = cancelled
        self.reason = reason            # short human string: why the loop ended
        self.polls = polls              # loop iterations run
        self.dropped_new = dropped_new  # total NEW parts the coordinator dropped

    def __repr__(self):
        return ("ScanResult(complete=%s, cancelled=%s, reason=%r, polls=%d, "
                "dropped_new=%d)" % (self.complete, self.cancelled, self.reason,
                                     self.polls, self.dropped_new))


def run_scan(decoder, scanner=None, *, poll_interval_ms=20,
             miss_warn_threshold=10, miss_warn_persist=2, timeout_ms=None,
             weighted_progress=True, completion_hold_ms=550,
             should_continue=None, on_progress=None, on_miss_warning=None,
             on_invalid=None, on_drop=None, log=None):
    """Drive the scan pipeline until the decoder completes or the loop is stopped.

    Assumes `scanner.start()` has already succeeded; does NOT call start()/stop()
    (lifecycle stays with the caller so it can also frame logging / UI around it).

    decoder             DecodeQR-like: add_data(bytes)->status int,
                        get_percent_complete()->int, is_complete->bool.
    scanner             the camera_scanner module (defaults to the imported one).
    poll_interval_ms    idle pause between poll rounds; the NEW ring is always
                        drained to empty first so this only paces the status read.
    miss_warn_threshold consecutive_misses value that means "found but unreadable"
                        (§7: measured runs hit ~32 when too far / out of focus; a
                        nudge around 10-20 helps before the user gives up).
    miss_warn_persist   require the threshold to hold this many consecutive reads
                        before firing (debounce a one-off spike).
    timeout_ms          optional wall-clock cap; None = run until complete/cancel.
    should_continue     optional callable()->bool; return False to cancel (e.g.
                        a back-button poll). Default: always continue.
    on_progress(pct, status)         new info added (PART_COMPLETE / COMPLETE).
    on_miss_warning(consecutive)     sustained-MISS crossed the threshold (once
                                     per episode; re-arms after it drops back).
    on_invalid(payload, why)         a NEW payload the decoder rejected (FALSE /
                                     INVALID) or that raised; why is the status
                                     int or the exception.
    on_drop(dropped_new)             the coordinator's dropped-NEW count grew
                                     (lost scan progress — never silent, §7a).
    log(*args)                       optional diagnostic sink.

    Returns a ScanResult.
    """
    cs = scanner if scanner is not None else _default_scanner
    if cs is None:
        raise RuntimeError("camera_scanner module unavailable (not on device?)")

    _cont = should_continue if should_continue is not None else (lambda: True)
    start_ms = _ticks_ms()
    polls = 0
    misses_over = 0        # consecutive reads at/over the miss threshold (debounce)
    warned = False         # miss warning already fired this episode
    last_dropped = 0       # last-seen coordinator dropped-NEW count
    progressed = [False]   # saw a PART_COMPLETE before COMPLETE (multi-part scan)
    max_pct = [0]          # monotonic clamp for the displayed percent

    # Segmented progress (BBQR / Specter indexed cycles): the SCREEN owns the decoded set —
    # announce the cycle size once, then stream one segment_event() per frame and the overlay
    # derives the percent from its own lit count, lighting cells out-of-order. UR/fountain +
    # single-frame QRs stay on the continuous report() path. begin_segments/segment_event are
    # the newer scanner surface; feature-detect so older firmware + the injected test fakes
    # fall back to the continuous bar. Gated on the decoder (is_segmented) so this module
    # needs no decode_qr import (see the four-constant note above).
    seg_supported = hasattr(cs, "begin_segments") and hasattr(cs, "segment_event")
    segmented = [False]    # currently driving the segmented bar (an indexed cycle)
    announced = [False]    # begin_segments() decision already made (segmented or not)

    def _emit(cb, *a):
        if cb is not None:
            cb(*a)

    def _percent():
        # Prefer the weighted UR estimate: it is based on recovered fragments
        # (+ partial mixed-frame credit), NOT processed_parts, so duplicate
        # re-reads can't inflate it toward the 0.99 cap (the default estimate's
        # flaw). BBQr/Specter ignore the flag and stay on honest collected/total.
        try:
            p = decoder.get_percent_complete(weight_mixed_frames=weighted_progress)
        except TypeError:  # a decoder (e.g. a test fake) with no weighted arg
            p = decoder.get_percent_complete()
        # The weighted estimate can momentarily dip (its own code notes this);
        # never let the bar go backwards.
        if p < max_pct[0]:
            p = max_pct[0]
        else:
            max_pct[0] = p
        return p

    def _segmented():
        # Decide segmented-vs-continuous once — after the first decode has resolved qr_type
        # and revealed the cycle total — then announce the cycle to the overlay. Returns True
        # while in segmented mode; idempotent (safe to call every frame).
        if announced[0]:
            return segmented[0]
        if not (seg_supported and getattr(decoder, "is_segmented", False)):
            announced[0] = True            # resolved as continuous; stop checking
            return False
        total = getattr(decoder, "total_segments", 0)
        if not total or total <= 0:
            return False                   # cycle size not revealed yet; re-check next frame
        cs.begin_segments(total)
        segmented[0] = True
        announced[0] = True
        return True

    while True:
        if not _cont():
            return ScanResult(decoder, False, True, "cancelled", polls, last_dropped)
        if timeout_ms is not None and _ticks_diff(_ticks_ms(), start_ms) >= timeout_ms:
            return ScanResult(decoder, False, True, "timeout", polls, last_dropped)
        polls += 1

        # 1. Drain the precious NEW ring FULLY (unique accumulation data; a drop
        #    is lost progress, so never leave any queued — §7a).
        while True:
            payload = cs.poll_new()
            if payload is None:
                break
            try:
                status = decoder.add_data(payload)
            except Exception as e:  # a malformed part must not kill the scan loop
                _emit(on_invalid, payload, e)
                continue

            pct = _percent()

            if status == _COMPLETE:
                # Terminal. Segmented: light the final piece so every cell is green (derived
                # 100%). Continuous: drive the fill to full + green.
                if _segmented():
                    cs.segment_event(cs.FRAME_NEW, decoder.last_part_number)
                else:
                    cs.report(cs.FRAME_NEW, 100)
                _emit(on_progress, 100, status)
                # Flush the completed bar before the hold. run_scan returns straight after
                # this with no further should_continue tick, so on the Pi — where LVGL only
                # advances when we pump — the final update (the just-lit final segment cell,
                # or the 100% fill) would be set but never rendered, leaving the last cell
                # blank / the bar short of full. _cont() pumps on the Pi; on MicroPython it's
                # a harmless input drain (the firmware display task renders on its own). Its
                # cancel return is irrelevant here — the scan is already complete.
                _cont()
                # Multi-part: hold briefly so the completed bar rests as a confirmation beat
                # before teardown. Single-part decoded in one frame with no prior progress —
                # snap and hand off immediately, no needless delay.
                if progressed[0] and completion_hold_ms:
                    _sleep_ms(completion_hold_ms)
                cs.report_complete()
                return ScanResult(decoder, True, False, "complete", polls, last_dropped)
            elif status == _PART_COMPLETE:
                progressed[0] = True
                if _segmented():
                    cs.segment_event(cs.FRAME_NEW, decoder.last_part_number)  # light the new cell
                else:
                    cs.report(cs.FRAME_NEW, pct)    # green dot, bar advances
                _emit(on_progress, pct, status)
            elif status == _PART_EXISTING:
                if segmented[0]:
                    # Re-read piece: mark it the current cell (white) — no new progress.
                    cs.segment_event(cs.FRAME_REPEAT, decoder.last_part_number)
                else:
                    cs.report(cs.FRAME_REPEAT, pct)  # gray dot, no new info
            else:  # _FALSE / _INVALID: decoded bytes, not a recognized format
                _emit(on_invalid, payload, status)

        # 2. Coalesced status: held-QR / idle dot + the sustained-MISS signal.
        st = cs.read_status()
        pct = _percent()

        # Dot for the non-NEW frames the drain above didn't cover. A held,
        # already-decoded QR streams REPEATs (no payload); an empty scene streams
        # NONE. MISS keeps the dot as-is (its signal is the counter, §6).
        if segmented[0]:
            # Dot-only update: no payload this round, so no piece index. segment_event with
            # piece_index = -1 touches only the frame dot, never the fill — so a held/idle
            # frame can't paint a continuous bar over the out-of-order cells.
            if st.latest == cs.FRAME_REPEAT or st.latest == cs.FRAME_NONE:
                cs.segment_event(st.latest, -1)
        elif st.latest == cs.FRAME_REPEAT:
            cs.report(cs.FRAME_REPEAT, pct)
        elif st.latest == cs.FRAME_NONE:
            cs.report(cs.FRAME_NONE, pct)

        # Lost-progress accounting: a grown dropped-NEW count is surfaced, never
        # silent (§7a "no silent truncation").
        if st.dropped_new != last_dropped:
            last_dropped = st.dropped_new
            _emit(on_drop, st.dropped_new)

        # Sustained MISS = "we keep locating a code but can't read it". The counter
        # is reset coordinator-side by ANY non-MISS frame, so it is already
        # miss-WITHOUT-progress; the consumer just thresholds + debounces it.
        if st.consecutive_misses >= miss_warn_threshold:
            misses_over += 1
            if misses_over >= miss_warn_persist and not warned:
                warned = True
                _emit(on_miss_warning, st.consecutive_misses)
        else:
            misses_over = 0
            warned = False  # re-arm once the run clears (a decode / look-away)

        if log is not None and (polls % 25) == 0:
            log("poll", polls, "pct", pct, "latest", st.latest,
                "cmiss", st.consecutive_misses, "dropped", st.dropped_new)

        _sleep_ms(poll_interval_ms)
