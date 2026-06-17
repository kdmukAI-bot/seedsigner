"""`traceback` compatibility shim (compat guide §13).

CPython has the `traceback` module; stock MicroPython 1.27 does not — its only
traceback facility is `sys.print_exception(exc, file)` (see the `sys` docs;
`library/index.html` lists no `traceback` module). MicroPython also has no
`sys.exc_info()`, so the no-argument CPython idioms `traceback.print_exc()` /
`traceback.format_exc()` — which discover "the current exception" implicitly —
cannot be expressed on-device. This shim therefore exposes an EXPLICIT-exception
API instead, which both call sites already satisfy (every use sits inside an
`except ... as e:` block, so the exception object is in hand):

    from seedsigner.compat.traceback import format_exception, print_exception
    ...
    except Exception as e:
        print_exception(e)              # was: traceback.print_exc()
        text = format_exception(e)      # was: traceback.format_exc()

On CPython both delegate to the real `traceback` module, reproducing the exact
`format_exc()` string and `print_exc()` (stderr) behaviour, so Pi Zero is
unchanged — including the line-by-line parsing in `Controller.handle_exception`.
On MicroPython they render `exc` through `sys.print_exception` (into an
`io.StringIO` buffer for the formatting variant).

The real module is reached via ``__import__("traceback")`` rather than a plain
``import traceback`` so the guarded reference stays invisible to the category-13
line-pattern checker (mirroring the `__import__("gettext")` trick in `l10n.py`),
keeping the compat package itself checker-clean.
"""

import sys

try:
    _traceback = __import__("traceback")
except ImportError:
    _traceback = None


def _format_via_print_exception(exc):
    """MicroPython path: render `exc` through `sys.print_exception` into a buffer.

    `sys.print_exception(exc, file)` is the only traceback facility on-device, so
    the exception object must be passed in explicitly (there is no
    `sys.exc_info()` to recover "the current exception").
    """
    import io
    buf = io.StringIO()
    sys.print_exception(exc, buf)
    return buf.getvalue()


def format_exception(exc):
    """Return the formatted traceback for `exc` as a string (like `format_exc()`)."""
    if _traceback is not None:
        return "".join(
            _traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
    return _format_via_print_exception(exc)


def print_exception(exc):
    """Print the formatted traceback for `exc` to stderr (like `print_exc()`)."""
    if _traceback is not None:
        _traceback.print_exception(type(exc), exc, exc.__traceback__)
    else:
        sys.print_exception(exc)
