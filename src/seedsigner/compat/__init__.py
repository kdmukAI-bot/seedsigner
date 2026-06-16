"""In-app compatibility layer for stock, unmodified MicroPython 1.27.

The business-logic tree (models, views, helpers, controller) must import and run
on both CPython 3.10 (Pi Zero) and stock MicroPython 1.27 (ESP32). A few
standard-library modules the app relies on behave differently — or do
nothing useful — on MicroPython. Rather than fake them in the platform (a
permanent, divergence-prone shim layer), each gets a thin in-app module here that
re-exports the real CPython implementation when present and falls back to a
minimal stub when it is not, selected once at import time via an in-app
``try/except ImportError`` that ships in app source and runs unchanged on the
stock interpreter.

Currently: ``l10n`` (gettext) — a passthrough on-device, where translation is
handled by the LVGL font/locale seam rather than ``.mo`` catalogs. (``logging``
is NOT here: it is a required frozen ``micropython-lib`` dependency the app
imports directly — see ``docs/micropython_compatibility.md`` §3.)

See ``docs/micropython_compatibility.md`` (the self-documenting checker is its
companion: ``tools/mpy_compat_check.py``).
"""
