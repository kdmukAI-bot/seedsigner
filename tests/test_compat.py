"""Tests for the in-app MicroPython compatibility shims (`seedsigner.compat`).

Currently the package holds the `l10n` (gettext) shim. Guarantees:

  1. Behaviour-preserving on CPython — the re-exported `gettext` names ARE the
     real `gettext` callables, so swapping a call site's import to the shim
     (mpy/04, across views, gui, and hardware) changes nothing on Pi Zero.
     Verified by identity.
  2. `set_locale()` writes the `LANGUAGE` env var that CPython's gettext reads,
     so locale changes still take effect on Pi Zero; on MicroPython (no env
     mapping) it falls back to `os.putenv`.
  3. The MicroPython fallback works — the passthrough/no-op functions are
     exercised directly (they are plain code that runs fine on CPython, so the
     absent-module branch is covered without a MicroPython build).

(`logging` is intentionally NOT shimmed — it is a required frozen micropython-lib
dependency the app imports directly; see docs/micropython_compatibility.md §3.)

This module has no hardware dependencies, so — unlike most of the suite — it
imports without `tests/base.py`'s mock scaffolding.
"""

import os
import types

import gettext as stdlib_gettext

from seedsigner.compat import l10n as compat_l10n


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
