"""Exercise the MicroPython branch of compat/l10n.py on the host.

CPython always takes the real-gettext branch, so the catalog-backed path (the one
that actually runs on the ESP32) would otherwise never be covered. This reimports
`compat.l10n` with stdlib `gettext` hidden — reproducing stock MicroPython — and
drives the real bootstrap sequence (`bindtextdomain` -> `set_locale`) against the
compiled catalogs.
"""
import builtins
import importlib
import os
import sys

import pytest


L10N_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "src", "seedsigner", "resources", "seedsigner-translations", "l10n",
)


@pytest.fixture
def mpy_l10n():
    """Yield a fresh compat.l10n imported as if stdlib gettext were absent."""
    modname = "seedsigner.compat.l10n"
    saved = sys.modules.pop(modname, None)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "gettext":
            raise ImportError("simulated MicroPython: no gettext")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = fake_import
    try:
        mod = importlib.import_module(modname)
        assert mod._gettext is None, "expected the MicroPython (no-gettext) branch"
        yield mod
    finally:
        builtins.__import__ = real_import
        # Restore the real CPython module so subsequent tests see stdlib gettext.
        sys.modules.pop(modname, None)
        if saved is not None:
            sys.modules[modname] = saved
        else:
            importlib.import_module(modname)


def _es_available():
    return os.path.exists(os.path.join(L10N_DIR, "es", "LC_MESSAGES", "messages.mo"))


def test_english_base_locale_passes_through(mpy_l10n):
    l = mpy_l10n
    l.bindtextdomain("messages", localedir=L10N_DIR)
    l.textdomain("messages")
    # English ships no catalog -> the source strings pass through unchanged.
    l.set_locale("en")
    assert l.gettext("Scan") == "Scan"
    assert l.ngettext("input", "inputs", 1) == "input"
    assert l.ngettext("input", "inputs", 2) == "inputs"


def test_spanish_catalog_translates(mpy_l10n):
    if not _es_available():
        pytest.skip("es catalog not compiled (run: python setup.py compile_catalog)")
    l = mpy_l10n
    l.bindtextdomain("messages", localedir=L10N_DIR)
    l.set_locale("es")
    assert l.gettext("Scan") == "Escanear"
    # ngettext dispatches to the catalog's 3-form Spanish rule without error.
    assert isinstance(l.ngettext("input", "inputs", 1), str)


def test_switch_to_missing_locale_fails_closed(mpy_l10n):
    if not _es_available():
        pytest.skip("es catalog not compiled")
    l = mpy_l10n
    l.bindtextdomain("messages", localedir=L10N_DIR)
    l.set_locale("es")
    assert l.gettext("Scan") == "Escanear"
    # Switching to a locale with no catalog reverts to the English passthrough.
    l.set_locale("zz-not-a-locale")
    assert l.gettext("Scan") == "Scan"


def test_setup_calls_return_stdlib_shapes(mpy_l10n):
    l = mpy_l10n
    assert l.bindtextdomain("messages", localedir="/tmp/l10n") == "/tmp/l10n"
    assert l.textdomain("messages") == "messages"


def test_gettext_before_set_locale_passes_through(mpy_l10n):
    # No catalog loaded yet -> passthrough, never an AttributeError.
    assert mpy_l10n.gettext("Settings") == "Settings"
    assert mpy_l10n.ngettext("input", "inputs", 3) == "inputs"
