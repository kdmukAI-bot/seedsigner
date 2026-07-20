"""Unit tests for the native camera-scan drive loop (hardware/scan_consumer.py).

`run_scan` is decoder-agnostic and scanner-injectable by design, so these
exercise it entirely on CPython with a fake scanner + fake decoder — no device
and no native `camera_scanner` module required.
"""
from collections import namedtuple, deque

from seedsigner.hardware.scan_consumer import (
    run_scan,
    ScanResult,
    _PART_COMPLETE,
    _PART_EXISTING,
    _COMPLETE,
    _FALSE,
)


# Mirrors the binding's read_status() attrtuple(latest, consecutive_misses, ...).
Status = namedtuple("Status", "latest consecutive_misses dropped_new has_corners")


class FakeScanner:
    """Stand-in for the `camera_scanner` C module.

    `rounds` scripts the NEW ring per poll round: round 0 is drained first, and
    each later round becomes available only after a read_status() call (which is
    what run_scan does once per loop pass, after fully draining the ring).
    """

    FRAME_NONE = 0
    FRAME_NEW = 1
    FRAME_REPEAT = 2
    FRAME_MISS = 3

    def __init__(self, rounds=None, status=None):
        self._rounds = deque(rounds or [])
        self._current = list(self._rounds.popleft()) if self._rounds else []
        self.status = status if status is not None else Status(self.FRAME_NONE, 0, 0, False)
        self.reports = []       # (frame_status, percent) tuples from report()
        self.completed = False  # report_complete() was called

    def poll_new(self):
        if self._current:
            return self._current.pop(0)
        return None

    def read_status(self):
        # Make the next scripted round available for the following drain pass.
        self._current = list(self._rounds.popleft()) if self._rounds else []
        return self.status

    def report(self, frame_status, percent):
        self.reports.append((frame_status, percent))

    def report_complete(self):
        self.completed = True


class FakeDecoder:
    """Returns scripted add_data() statuses; percent tracks the call count."""

    def __init__(self, statuses, percents=None):
        self._statuses = list(statuses)
        self._percents = list(percents) if percents else None
        self._idx = 0
        self.seen = []

    def add_data(self, payload):
        self.seen.append(payload)
        status = self._statuses[self._idx]
        self._idx += 1
        return status

    def get_percent_complete(self, weight_mixed_frames=False):
        if self._percents is None:
            return 0
        i = min(self._idx, len(self._percents)) - 1
        return self._percents[i] if i >= 0 else 0

    @property
    def is_complete(self):
        # run_scan keys off add_data()'s returned status, never this.
        return False


def _counter_should_continue(max_rounds):
    """should_continue that permits `max_rounds` loop passes, then cancels."""
    state = {"n": 0}

    def should_continue():
        state["n"] += 1
        return state["n"] <= max_rounds

    return should_continue


def test_single_part_complete():
    scanner = FakeScanner(rounds=[[b"payload"]])
    decoder = FakeDecoder(statuses=[_COMPLETE], percents=[100])
    progress = []

    result = run_scan(
        decoder, scanner=scanner, poll_interval_ms=0, completion_hold_ms=0,
        on_progress=lambda pct, st: progress.append((pct, st)),
    )

    assert isinstance(result, ScanResult)
    assert result.complete is True
    assert result.cancelled is False
    assert result.reason == "complete"
    assert scanner.completed is True
    assert decoder.seen == [b"payload"]
    assert progress == [(100, _COMPLETE)]


def test_multi_part_then_complete():
    # Both parts arrive in one drain round: PART_COMPLETE then COMPLETE.
    scanner = FakeScanner(rounds=[[b"p1", b"p2"]])
    decoder = FakeDecoder(statuses=[_PART_COMPLETE, _COMPLETE], percents=[40, 100])
    progress = []

    result = run_scan(
        decoder, scanner=scanner, poll_interval_ms=0, completion_hold_ms=0,
        on_progress=lambda pct, st: progress.append((pct, st)),
    )

    assert result.complete is True
    assert result.reason == "complete"
    assert scanner.completed is True
    # One progress callback for the partial, one for completion.
    assert progress == [(40, _PART_COMPLETE), (100, _COMPLETE)]


def test_cancel_via_should_continue():
    scanner = FakeScanner(rounds=[[b"p1"]])
    decoder = FakeDecoder(statuses=[_PART_COMPLETE], percents=[10])

    result = run_scan(
        decoder, scanner=scanner, poll_interval_ms=0,
        should_continue=lambda: False,
    )

    assert result.cancelled is True
    assert result.complete is False
    assert result.reason == "cancelled"
    # Cancel is checked at the loop top, before the ring is drained.
    assert decoder.seen == []
    assert scanner.completed is False


def test_timeout():
    scanner = FakeScanner()
    decoder = FakeDecoder(statuses=[])

    result = run_scan(decoder, scanner=scanner, poll_interval_ms=0, timeout_ms=0)

    assert result.cancelled is True
    assert result.complete is False
    assert result.reason == "timeout"
    assert decoder.seen == []


def test_invalid_payload_reported_then_scan_continues():
    # A FALSE payload fires on_invalid but must not end the scan; a later
    # COMPLETE still finishes it.
    scanner = FakeScanner(rounds=[[b"junk", b"good"]])
    decoder = FakeDecoder(statuses=[_FALSE, _COMPLETE], percents=[0, 100])
    invalids = []

    result = run_scan(
        decoder, scanner=scanner, poll_interval_ms=0, completion_hold_ms=0,
        on_invalid=lambda payload, why: invalids.append((payload, why)),
    )

    assert result.complete is True
    assert invalids == [(b"junk", _FALSE)]


def test_add_data_exception_does_not_kill_loop():
    class BoomDecoder(FakeDecoder):
        def add_data(self, payload):
            self.seen.append(payload)
            raise ValueError("boom")

    scanner = FakeScanner(rounds=[[b"x"]])
    decoder = BoomDecoder(statuses=[])
    invalids = []

    result = run_scan(
        decoder, scanner=scanner, poll_interval_ms=0,
        should_continue=_counter_should_continue(1),  # one drain pass, then cancel
        on_invalid=lambda payload, why: invalids.append((payload, why)),
    )

    assert result.cancelled is True
    assert len(invalids) == 1
    assert invalids[0][0] == b"x"
    assert isinstance(invalids[0][1], ValueError)


def test_sustained_miss_warning_fires_once():
    high_miss = Status(FakeScanner.FRAME_MISS, 15, 0, False)  # >= threshold
    scanner = FakeScanner(status=high_miss)  # poll_new always empty
    decoder = FakeDecoder(statuses=[])
    warns = []

    result = run_scan(
        decoder, scanner=scanner, poll_interval_ms=0,
        miss_warn_threshold=10, miss_warn_persist=2,
        should_continue=_counter_should_continue(4),
        on_miss_warning=lambda n: warns.append(n),
    )

    assert result.cancelled is True
    # Debounced (persist=2) and latched: one warning despite several miss rounds.
    assert warns == [15]


# ── Segmented (indexed-cycle) progress: BBQR / Specter ──────────────────────
# The scanner grows two methods (begin_segments/segment_event) and the decoder
# exposes is_segmented + total_segments + a 0-based last_part_number. run_scan
# announces the cycle once, then streams one segment_event() per frame and never
# touches the continuous report() bar (which would fight the out-of-order cells).

class SegmentedFakeScanner(FakeScanner):
    """FakeScanner + the indexed-cycle surface (begin_segments/segment_event)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.begun = []           # total_segments values passed to begin_segments()
        self.segment_events = []  # (status, piece_index) tuples

    def begin_segments(self, total_segments):
        self.begun.append(total_segments)

    def segment_event(self, status, piece_index):
        self.segment_events.append((status, piece_index))


class SegmentedFakeDecoder(FakeDecoder):
    """FakeDecoder for an indexed cycle: is_segmented + total_segments + a scripted
    0-based last_part_number per add_data() call (set for new AND repeat frames)."""

    def __init__(self, statuses, parts, total_segments, percents=None):
        super().__init__(statuses, percents=percents)
        self._parts = list(parts)
        self.total_segments = total_segments
        self.last_part_number = -1
        self.is_segmented = True

    def add_data(self, payload):
        status = super().add_data(payload)  # advances self._idx
        self.last_part_number = self._parts[self._idx - 1]
        return status


def test_segmented_indexed_cycle_out_of_order():
    # A 3-piece cycle scanned out of order: piece 2 (new), piece 0 (new), piece 2
    # again (re-read), piece 1 (new -> COMPLETE). One drain round.
    scanner = SegmentedFakeScanner(rounds=[[b"p2", b"p0", b"p2", b"p1"]])
    decoder = SegmentedFakeDecoder(
        statuses=[_PART_COMPLETE, _PART_COMPLETE, _PART_EXISTING, _COMPLETE],
        parts=[2, 0, 2, 1],
        total_segments=3,
        percents=[33, 66, 66, 100],
    )

    result = run_scan(decoder, scanner=scanner, poll_interval_ms=0, completion_hold_ms=0)

    assert result.complete is True
    assert scanner.completed is True                 # report_complete() still terminal
    assert scanner.begun == [3]                       # announced exactly once
    assert scanner.segment_events == [
        (FakeScanner.FRAME_NEW, 2),                   # new piece 2
        (FakeScanner.FRAME_NEW, 0),                   # new piece 0
        (FakeScanner.FRAME_REPEAT, 2),                # re-read piece 2 (current cell)
        (FakeScanner.FRAME_NEW, 1),                   # final piece -> all lit
    ]
    assert scanner.reports == []                      # never the continuous bar


def test_segmented_decoder_falls_back_when_scanner_lacks_support():
    # An indexed-cycle decoder, but the scanner (old firmware / plain fake) has no
    # begin_segments/segment_event -> stay on the continuous report() path, no crash.
    scanner = FakeScanner(rounds=[[b"p0", b"p1"]])    # report()/report_complete() only
    decoder = SegmentedFakeDecoder(
        statuses=[_PART_COMPLETE, _COMPLETE],
        parts=[0, 1],
        total_segments=2,
        percents=[50, 100],
    )

    result = run_scan(decoder, scanner=scanner, poll_interval_ms=0, completion_hold_ms=0)

    assert result.complete is True
    assert scanner.completed is True
    assert scanner.reports == [
        (FakeScanner.FRAME_NEW, 50),
        (FakeScanner.FRAME_NEW, 100),
    ]
