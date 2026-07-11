"""Shared skip guard for tests that drive the native-cUR (``uUR``) UR path.

The pure-Python ``ur2`` runtime fallback was removed from the seams (single-decoder cutover),
so the UR encode/decode path now requires the compiled native module. Tests that exercise it
skip when ``uUR`` isn't built — the common local-dev case — so ``pytest`` stays green without a
native build. They run on-device, in CI once it builds cUR, and on a dev machine that has
compiled the cUR CPython binding (``python/uUR.c`` + ``src/*.c``) into its venv.

The build-free ``tests/test_ur_native_adapters.py`` fake-``uUR`` tests still cover the adapter
glue regardless, so the seam logic keeps automated coverage even when ``uUR`` is absent.
"""
import importlib.util

import pytest

HAS_NATIVE_UR = importlib.util.find_spec("uUR") is not None

requires_native_ur = pytest.mark.skipif(
    not HAS_NATIVE_UR,
    reason="native cUR (uUR) not built; the pure-Python ur2 fallback was removed",
)
