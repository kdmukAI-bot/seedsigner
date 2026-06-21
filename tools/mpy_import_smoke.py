"""
MicroPython import smoke test — "it loads on stock MicroPython".

Runs ON a stock, unmodified MicroPython 1.27 Unix port (built in CI from the
same v1.27.0 the firmware pins). For every module in the keep-set — the exact set
the static checker scans, emitted by ``mpy_compat_check.py --list-modules`` — it
attempts a real ``import`` and reports one of:

  OK    — imported on stock MicroPython.
  SKIP  — failed *only* because a dependency that is not yet available in this
          smoke environment is absent (see EXPECTED_MISSING). These flip to OK
          for free once the dependency is wired in; they are not failures.
  FAIL  — any other error (a real incompatibility the static AST checker can't
          see: an absent stdlib member, a runtime-only construct, a bad import).

Exit code is non-zero iff there is at least one FAIL, so CI gates on real
breakage while the not-yet-ported deps (notably embit) keep the rest honest.

Limitation: a module that imports an EXPECTED_MISSING dependency *before* hitting
a genuine incompatibility is reported SKIP — its real problems surface once that
dependency lands. Every dependency-free module is covered fully today.

Usage (run by the MicroPython interpreter, not CPython):
    micropython tools/mpy_import_smoke.py <keepset_file> <syspath_dir> [<syspath_dir> ...]

    <keepset_file>  newline-delimited dotted module names (from --list-modules)
    <syspath_dir>   directories prepended to sys.path: the source root (so
                    `seedsigner` is importable) and the location of any required
                    frozen package staged for the smoke (e.g. micropython-lib
                    `logging`).
"""
import sys


# Dependencies that are legitimately absent in the smoke environment, so an
# ImportError naming one is an expected SKIP rather than a real failure. Mirrors
# the static checker's classification (tools/mpy_compat_check.py):
#   - BEING_REPLACED ({PIL, Pillow, pyzbar, qrcode}) — never ported; LVGL/native
#     replace them, and their gui/ users are outside the keep-set anyway.
#   - KNOWN_COMPATIBLE ({embit}) — has a MicroPython port, simply not yet wired
#     into this smoke. This is the big one: it gates the seed/PSBT layer.
#   - numpy — a lazy hardware-path dependency; defensive (hardware/ is excluded).
EXPECTED_MISSING = ("embit", "PIL", "Pillow", "pyzbar", "qrcode", "numpy")


# Pre-existing real incompatibilities this smoke surfaced, grandfathered so the
# gate guards against NEW breakage immediately (mirrors `allowed_files` in the
# static checker's baseline — the gate only ever tightens). Each entry routes to
# the compat layer that will fix it; remove the entry in the same change that
# lands the fix. A module here that *passes* is reported so the entry can be
# dropped — it does not fail the build (avoids ratchet flakiness).
#   - models.decode_qr: `import zlib` — MicroPython 1.27 ships `deflate`, not
#     `zlib` (decode_qr.py uses zlib.decompressobj). Fix routes to a stdlib swap
#     (mpy/02-stdlib-swaps): a compat.zlib over `deflate`, or migrate the call.
#   - views.scan_views: a complex f-string (nested same-type quotes + a `\"`
#     backslash escape) the MicroPython f-string parser rejects. Fix routes to
#     the syntax layers (mpy/01/02): simplify the f-string.
KNOWN_FAILURES = {
    "seedsigner.models.decode_qr": "import zlib (use deflate) -> mpy/02-stdlib-swaps",
    "seedsigner.views.scan_views": "complex f-string unsupported -> mpy/01-02 syntax",
}


def _missing_module_name(exc):
    """Best-effort extract the missing module name from an ImportError.

    Stock MicroPython raises ``ImportError: no module named 'embit'``. Return the
    quoted name, or None if the message doesn't carry one.
    """
    msg = str(exc)
    start = msg.find("'")
    if start == -1:
        return None
    end = msg.find("'", start + 1)
    if end == -1:
        return None
    return msg[start + 1:end]


def _is_expected_missing(exc):
    name = _missing_module_name(exc)
    if name is None:
        # Fall back to a substring match so an oddly-formatted message still
        # classifies a known-absent dependency correctly.
        msg = str(exc)
        return any(dep in msg for dep in EXPECTED_MISSING)
    top = name.split(".")[0]
    return top in EXPECTED_MISSING


def main():
    if len(sys.argv) < 3:
        print("usage: micropython mpy_import_smoke.py <keepset_file> <syspath_dir> [...]")
        return 2

    keepset_file = sys.argv[1]
    for path_dir in sys.argv[2:]:
        sys.path.insert(0, path_dir)

    with open(keepset_file) as f:
        modules = [line.strip() for line in f if line.strip()]

    ok = []
    skipped = []
    xfailed = []        # grandfathered known failures (do not gate)
    now_passing = []    # grandfathered, but now importing — drop from KNOWN_FAILURES
    failed = []         # real, un-grandfathered failures (gate)

    def _record_failure(module, err):
        if module in KNOWN_FAILURES:
            xfailed.append(module)
            print("XFAIL " + module + "  -> " + err + "  [" + KNOWN_FAILURES[module] + "]")
        else:
            failed.append((module, err))
            print("FAIL  " + module + "  -> " + err)

    for module in modules:
        try:
            __import__(module)
            if module in KNOWN_FAILURES:
                now_passing.append(module)
                print("OK*   " + module + "  (grandfathered failure now passes; drop from KNOWN_FAILURES)")
            else:
                ok.append(module)
                print("OK    " + module)
        except ImportError as e:
            if _is_expected_missing(e) and module not in KNOWN_FAILURES:
                dep = _missing_module_name(e) or "?"
                skipped.append(module)
                print("SKIP  " + module + "  (needs " + dep + ")")
            else:
                _record_failure(module, repr(e))
        except Exception as e:
            # A non-import error at import time is a real incompatibility
            # (e.g. an absent stdlib attribute touched at module load).
            _record_failure(module, repr(e))

    print("")
    print("import smoke: {} ok, {} skipped (deps absent), {} xfail (known), {} failed / {} total".format(
        len(ok), len(skipped), len(xfailed), len(failed), len(modules)))
    if now_passing:
        print("NOTE: grandfathered modules now importing — remove from KNOWN_FAILURES:")
        for module in now_passing:
            print("  " + module)
    if failed:
        print("FAILURES (real, gating):")
        for module, err in failed:
            print("  " + module + ": " + err)
        return 1
    return 0


sys.exit(main())
