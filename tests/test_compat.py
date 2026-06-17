"""Tests for the in-app MicroPython compatibility shims (`seedsigner.compat`).

The package holds the `l10n` (gettext), `threading`, and `traceback` shims. Each
follows the same two-sided guarantee:

  1. Behaviour-preserving on CPython — the re-exported names ARE the real stdlib
     callables (verified by identity for l10n/threading; by output equality for
     traceback), so swapping a call site's import to the shim changes nothing on
     Pi Zero.
  2. The MicroPython fallback works — the device-path code is plain Python that
     runs on CPython too (`_thread` exists on CPython; `sys.print_exception` is
     faked), so the absent-module branch is covered without a MicroPython build.
     For `threading` this includes REAL concurrency validation: threads spawned
     via `_thread` actually run, `is_alive()` transitions correctly, and a lock
     serializes contended access.

Specifics:
  * l10n: the gettext names are identity-equal to the stdlib's; `set_locale()`
    writes the `LANGUAGE` env var CPython's gettext reads (falls back to
    `os.putenv` where there is no env mapping); passthrough/no-op stubs work.
  * threading: `Thread`/`Lock` are the stdlib's on CPython; the `_MpThread`
    workalike (over `_thread.start_new_thread`) and `_thread.allocate_lock()`
    are exercised under real concurrency.
  * traceback: `format_exception`/`print_exception` reproduce `format_exc()` /
    `print_exc()` on CPython and fall back to `sys.print_exception` on-device.

(`logging` is intentionally NOT shimmed — it is a required frozen micropython-lib
dependency the app imports directly; see docs/micropython_compatibility.md §3.)

This module has no hardware dependencies, so — unlike most of the suite — it
imports without `tests/base.py`'s mock scaffolding.
"""

import _thread
import os
import time
import types

import pytest

import gettext as stdlib_gettext
import threading as stdlib_threading
import traceback as stdlib_traceback

from seedsigner.compat import l10n as compat_l10n
from seedsigner.compat import threading as compat_threading
from seedsigner.compat import traceback as compat_traceback


# ---------------------------------------------------------------------------
# Shared helper — poll a predicate (the app's own "join-equivalent" idiom)
# ---------------------------------------------------------------------------

def _wait_until(predicate, timeout=5.0):
    """Spin on `predicate` up to `timeout` seconds; return its final truthiness.

    Mirrors how the app waits for a thread to drain (poll `is_alive()`, never
    `join()`), so the tests exercise the same contract the device relies on.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


# ---------------------------------------------------------------------------
# l10n shim — CPython path (behaviour-preserving)
# ---------------------------------------------------------------------------

def test_gettext_is_real_on_cpython():
    assert compat_l10n.gettext is stdlib_gettext.gettext
    assert compat_l10n.ngettext is stdlib_gettext.ngettext
    assert compat_l10n.bindtextdomain is stdlib_gettext.bindtextdomain
    assert compat_l10n.textdomain is stdlib_gettext.textdomain


def test_gettext_returns_source_when_untranslated():
    # With no catalog providing it, real gettext returns the message unchanged —
    # the same observable result as the MicroPython passthrough.
    msg = "an-untranslated-unique-sentinel-string"
    assert compat_l10n.gettext(msg) == msg


def test_domain_setup_callables_do_not_raise(tmp_path):
    compat_l10n.bindtextdomain("messages", localedir=str(tmp_path))
    compat_l10n.textdomain("messages")


# ---------------------------------------------------------------------------
# set_locale — drives the LANGUAGE env var gettext's catalog lookup reads
# ---------------------------------------------------------------------------

def test_set_locale_writes_language_env_on_cpython(monkeypatch):
    # CPython's gettext.find() reads the LANGUAGE env var from os.environ, so
    # set_locale must land the value there — os.putenv alone would be invisible
    # to gettext and translation would silently stop following the locale.
    monkeypatch.setenv("LANGUAGE", "placeholder")
    compat_l10n.set_locale("xx_YY")
    assert os.environ["LANGUAGE"] == "xx_YY"


def test_set_locale_falls_back_to_putenv_without_environ(monkeypatch):
    # Simulate stock MicroPython: an `os` exposing putenv() but no environ map.
    recorded = {}
    fake_os = types.SimpleNamespace(putenv=lambda k, v: recorded.__setitem__(k, v))
    monkeypatch.setattr(compat_l10n, "os", fake_os)
    compat_l10n.set_locale("xx_YY")
    assert recorded == {"LANGUAGE": "xx_YY"}


# ---------------------------------------------------------------------------
# l10n shim — MicroPython stub path (exercised directly)
# ---------------------------------------------------------------------------

def test_l10n_identity_passthrough():
    assert compat_l10n._identity("verbatim") == "verbatim"


def test_l10n_ngettext_passthrough_picks_form():
    # No catalogs on-device, so the passthrough picks by the English plural rule.
    assert compat_l10n._identity_ngettext("input", "inputs", 1) == "input"
    assert compat_l10n._identity_ngettext("input", "inputs", 2) == "inputs"
    assert compat_l10n._identity_ngettext("input", "inputs", 0) == "inputs"


def test_l10n_noop_domain_setters():
    assert compat_l10n._noop_bindtextdomain("messages", "/some/dir") == "/some/dir"
    assert compat_l10n._noop_bindtextdomain("messages") is None
    assert compat_l10n._noop_textdomain("messages") == "messages"
    assert compat_l10n._noop_textdomain() is None


# ===========================================================================
# threading shim
# ===========================================================================

# ---------------------------------------------------------------------------
# CPython path (behaviour-preserving) — the names ARE the stdlib's
# ---------------------------------------------------------------------------

def test_threading_is_real_on_cpython():
    assert compat_threading.Thread is stdlib_threading.Thread
    assert compat_threading.Lock is stdlib_threading.Lock


def test_lock_works_as_context_manager_and_acquire_release():
    # The whole surface the app uses: `with lock:` and bare acquire()/release().
    lock = compat_threading.Lock()
    with lock:
        pass
    assert lock.acquire() is True
    lock.release()


# ---------------------------------------------------------------------------
# MicroPython path — `_MpThread` over `_thread`, exercised under real concurrency
#
# `_MpThread` is the on-device Thread; it is plain code that runs on CPython too
# (CPython also ships the low-level `_thread`), so the device path is genuinely
# validated here without a MicroPython build — this is the concurrency proof the
# threading layer turns on, not just an import smoke test.
# ---------------------------------------------------------------------------

def test_mpthread_not_alive_before_start():
    t = compat_threading._MpThread(target=lambda: None)
    assert t.is_alive() is False


def test_mpthread_runs_and_is_alive_transitions():
    state = {"ran": False, "loop": True}

    class Worker(compat_threading._MpThread):
        def run(self):
            state["ran"] = True
            while state["loop"]:
                time.sleep(0.005)

    t = Worker()
    t.start()
    assert _wait_until(lambda: state["ran"]), "thread body never executed"
    assert t.is_alive() is True               # alive while the run() loop spins
    state["loop"] = False                      # ask it to finish (stop()-style)
    # poll is_alive() -> False: the exact "join-equivalent" the app uses
    assert _wait_until(lambda: not t.is_alive()), "is_alive() never went False"


def test_mpthread_passes_target_args_and_kwargs():
    captured = {}

    def target(a, b, c=0):
        captured["sum"] = a + b + c

    t = compat_threading._MpThread(target=target, args=(2, 3), kwargs={"c": 5})
    t.start()
    assert _wait_until(lambda: "sum" in captured)
    assert captured["sum"] == 10
    assert _wait_until(lambda: not t.is_alive())


# The raised exception is intentional and goes uncaught in the spawned thread
# (faithful to real Thread/`_thread` semantics: the thread dies and its traceback
# is printed) — pytest reports that as an unraisable-exception warning, which is
# expected here, not a defect.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_mpthread_is_alive_drops_to_false_even_if_run_raises(capsys):
    # The `finally` in _bootstrap must flip the flag even when run() raises, or
    # the app's is_alive() drain-poll would hang forever on a crashed thread.
    def boom():
        raise RuntimeError("intentional")

    t = compat_threading._MpThread(target=boom)
    t.start()
    assert _wait_until(lambda: not t.is_alive())
    capsys.readouterr()  # swallow the thread's traceback printed to stderr


def test_mpthread_stores_daemon_flag():
    # `daemon` is accepted/stored (a no-op on-device); BaseThread passes it.
    assert compat_threading._MpThread(daemon=True).daemon is True
    assert compat_threading._MpThread().daemon is None


def test_thread_allocate_lock_supports_context_manager():
    # On MicroPython `Lock` resolves to `_thread.allocate_lock`; its lock must
    # support the `with` protocol (the app uses `with renderer.lock:` heavily).
    lock = _thread.allocate_lock()
    assert lock.locked() is False
    with lock:
        assert lock.locked() is True
    assert lock.locked() is False


def test_mpy_lock_serializes_contended_mpthreads():
    # The reason Lock exists on-device: serialize read-modify-write on shared
    # state across `_thread`-spawned threads. Use the actual device primitive
    # (`_thread.allocate_lock()`) and many contended increments — without the
    # lock this loses updates; with it, every increment must land.
    lock = _thread.allocate_lock()
    shared = {"n": 0}
    n_threads = 8
    iters = 1000
    done = []

    def worker():
        for _ in range(iters):
            with lock:
                shared["n"] += 1
        done.append(True)

    threads = [compat_threading._MpThread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    assert _wait_until(lambda: len(done) == n_threads, timeout=10.0), "threads did not all finish"
    assert shared["n"] == n_threads * iters


# ===========================================================================
# traceback shim
# ===========================================================================

# ---------------------------------------------------------------------------
# CPython path (behaviour-preserving) — reproduces format_exc()/print_exc()
# ---------------------------------------------------------------------------

def test_format_exception_matches_stdlib_format_exc_on_cpython():
    try:
        raise ValueError("boom")
    except ValueError as e:
        expected = "".join(
            stdlib_traceback.format_exception(type(e), e, e.__traceback__)
        )
        assert compat_traceback.format_exception(e) == expected


def test_format_exception_last_line_is_parseable_type_and_message():
    # Controller.handle_exception parses splitlines()[-1] as "Type: message" and
    # scans for ", line " — assert the shape that parsing depends on.
    try:
        raise KeyError("missing key")
    except KeyError as e:
        formatted = compat_traceback.format_exception(e)
    last_line = formatted.splitlines()[-1]
    assert last_line.split(":")[0].split(".")[-1] == "KeyError"
    assert any(", line " in line for line in formatted.splitlines())


def test_print_exception_writes_to_stderr_on_cpython(capsys):
    try:
        raise ValueError("printed-to-stderr")
    except ValueError as e:
        compat_traceback.print_exception(e)
    assert "ValueError: printed-to-stderr" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# MicroPython path — no `traceback` module; render via `sys.print_exception`
# ---------------------------------------------------------------------------

def test_format_exception_falls_back_to_sys_print_exception(monkeypatch):
    # Simulate stock MicroPython: no `traceback` module, but a `sys` exposing
    # print_exception(exc, file) — the shim routes formatting through a buffer.
    def fake_print_exception(exc, file):
        file.write("Traceback (most recent call last):\n")
        file.write("{}: {}\n".format(type(exc).__name__, exc))

    fake_sys = types.SimpleNamespace(print_exception=fake_print_exception)
    monkeypatch.setattr(compat_traceback, "_traceback", None)
    monkeypatch.setattr(compat_traceback, "sys", fake_sys)

    out = compat_traceback.format_exception(AssertionError("simulated"))
    assert "AssertionError: simulated" in out


def test_print_exception_falls_back_to_sys_print_exception(monkeypatch):
    recorded = {}

    def fake_print_exception(exc):
        recorded["exc"] = exc

    fake_sys = types.SimpleNamespace(print_exception=fake_print_exception)
    monkeypatch.setattr(compat_traceback, "_traceback", None)
    monkeypatch.setattr(compat_traceback, "sys", fake_sys)

    err = RuntimeError("device-path")
    compat_traceback.print_exception(err)
    assert recorded["exc"] is err
