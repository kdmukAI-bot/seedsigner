"""Tests for the mpy/07 cryptography compat shims.

`seedsigner.compat.base64` and `seedsigner.compat.hmac` delegate to the real
stdlib on CPython, so the MicroPython fallback branches (pure-Python base32,
`binascii`-backed base64, `hmac.new(...)` instead of `hmac.digest(...)`) would
never run under the CPython test suite. These tests force those branches with
monkeypatching and assert they match the stdlib output byte-for-byte — that is
the behaviour the device relies on.
"""

import base64 as std_base64
import hashlib
import hmac as std_hmac

import pytest

from seedsigner.compat import base64 as compat_base64
from seedsigner.compat import hmac as compat_hmac


SAMPLES = [
    b"",
    b"f",
    b"fo",
    b"foo",
    b"foob",
    b"fooba",
    b"foobar",
    b"hello world\x00\x01\x02\xff",
    bytes(range(40)),
]


# ---------------------------------------------------------------------------
# base64 / base32
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("data", SAMPLES)
def test_base64_cpython_path_matches_stdlib(data):
    assert compat_base64.b64encode(data) == std_base64.b64encode(data)
    assert compat_base64.b64decode(std_base64.b64encode(data)) == data
    assert compat_base64.b32decode(std_base64.b32encode(data)) == data


@pytest.mark.parametrize("data", SAMPLES)
def test_base64_micropython_fallback_matches_stdlib(monkeypatch, data):
    # Force the MicroPython branch (binascii base64 + pure-Python base32).
    monkeypatch.setattr(compat_base64, "_base64", None)
    assert compat_base64.b64encode(data) == std_base64.b64encode(data)
    assert compat_base64.b64decode(std_base64.b64encode(data)) == data
    # base32 input is uppercase with padding (matches the BBQR call site).
    assert compat_base64.b32decode(std_base64.b32encode(data)) == data
    # str input is accepted too.
    assert compat_base64.b32decode(std_base64.b32encode(data).decode("ascii")) == data


def test_base32_fallback_rejects_invalid_char(monkeypatch):
    monkeypatch.setattr(compat_base64, "_base64", None)
    with pytest.raises(ValueError):
        compat_base64.b32decode("!!!!!!!!")


# ---------------------------------------------------------------------------
# hmac
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("digestmod", [hashlib.sha512, hashlib.sha256])
def test_hmac_cpython_path_matches_stdlib(digestmod):
    key, msg = b"Seed version", b"the quick brown fox"
    assert compat_hmac.digest(key, msg, digestmod) == std_hmac.digest(key, msg, digestmod)


def test_hmac_fallback_uses_new(monkeypatch):
    # embit's MicroPython hmac may expose only hmac.new(), not the one-shot
    # hmac.digest(); force that path and confirm it still matches the stdlib.
    class _NewOnlyHmac:
        new = staticmethod(std_hmac.new)

    monkeypatch.setattr(compat_hmac, "_hmac", _NewOnlyHmac)
    assert not hasattr(_NewOnlyHmac, "digest")
    key, msg = b"Seed version", b"the quick brown fox"
    assert compat_hmac.digest(key, msg, hashlib.sha512) == std_hmac.digest(key, msg, hashlib.sha512)


def test_hmac_missing_module_raises(monkeypatch):
    monkeypatch.setattr(compat_hmac, "_hmac", None)
    with pytest.raises(ImportError):
        compat_hmac.digest(b"k", b"m", hashlib.sha512)
