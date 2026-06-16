"""Tests for the in-app MicroPython compatibility shims (`seedsigner.compat`).

Currently the package holds the `l10n` (gettext) shim. Two guarantees:

  1. Behaviour-preserving on CPython — the public names ARE the real `gettext`
     callables, so swapping a view's import to the shim (mpy/05) changes nothing
     on Pi Zero. Verified by identity.
  2. The MicroPython fallback works — the passthrough/no-op functions are
     exercised directly (they are plain code that runs fine on CPython, so the
     absent-module branch is covered without a MicroPython build).

(`logging` is intentionally NOT shimmed — it is a required frozen micropython-lib
dependency the app imports directly; see docs/micropython_compatibility.md §3.)

This module has no hardware dependencies, so — unlike most of the suite — it
imports without `tests/base.py`'s mock scaffolding.
"""

import gettext as stdlib_gettext

from seedsigner.compat import l10n as compat_l10n


# ---------------------------------------------------------------------------
# l10n shim — CPython path (behaviour-preserving)
# ---------------------------------------------------------------------------

def test_gettext_is_real_on_cpython():
    assert compat_l10n.gettext is stdlib_gettext.gettext
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
# l10n shim — MicroPython stub path (exercised directly)
# ---------------------------------------------------------------------------

def test_l10n_identity_passthrough():
    assert compat_l10n._identity("verbatim") == "verbatim"


def test_l10n_noop_domain_setters():
    assert compat_l10n._noop_bindtextdomain("messages", "/some/dir") == "/some/dir"
    assert compat_l10n._noop_bindtextdomain("messages") is None
    assert compat_l10n._noop_textdomain("messages") == "messages"
    assert compat_l10n._noop_textdomain() is None
