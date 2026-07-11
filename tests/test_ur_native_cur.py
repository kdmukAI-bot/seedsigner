"""Real native-cUR (`uUR`) round-trip + parity tests.

Unlike ``test_ur_native_adapters.py`` (which fakes ``uUR`` to pin the *adapter glue* on any
host), these exercise the **actual** compiled native module, so they only run where ``uUR``
has been built. That is the case on-device (ESP32/Pi firmware), in CI once it builds cUR, and
on a dev machine that has compiled the CPython binding (cUR ``python/uUR.c`` + ``src/*.c``).

They are skipped when ``uUR`` is absent — the common local-dev case — so ``pytest`` stays green
without a native build; the build-free adapter-contract tests still cover the seam logic. When
``uUR`` *is* importable, the seams (``helpers/ur2/decoder.py`` / ``encoder.py``) auto-select the
native adapter, so importing ``URDecoder`` / ``UREncoder`` from them here drives real cUR.

To run these locally: build the cUR CPython binding into your venv so ``import uUR`` works (see
the cUR repo's ``python/`` binding + build notes), then re-run ``pytest``.
"""
import pytest

# Skip the whole module unless the native module is present.
pytest.importorskip("uUR", reason="native cUR (uUR) not built; skipping real-cUR tests")

from seedsigner.helpers.ur2.decoder import URDecoder
from seedsigner.helpers.ur2.encoder import UREncoder
from seedsigner.helpers.ur2.ur import UR
from seedsigner.helpers.ur2.ur_encoder import UREncoder as _Ur2Encoder


def _payload(n=200):
    return bytearray((i * 7 + 1) & 0xFF for i in range(n))


def test_native_cur_encode_decode_roundtrip():
    """Native encode -> native decode reassembles the original CBOR (the core correctness
    property, guaranteed by the BC-UR spec)."""
    msg = _payload()
    enc = UREncoder(ur=UR("bytes", msg), max_fragment_len=30)
    n = enc.fountain_encoder.seq_len()

    dec = URDecoder()
    for _ in range(n):
        assert dec.receive_part(enc.next_part()) is True
    assert dec.is_complete() is True
    assert bytes(dec.result_message().cbor) == bytes(msg)


def test_native_cur_parts_match_pure_python():
    """Strict cross-implementation parity: the native encoder's pure parts are byte-identical
    to the pure-Python ur2 encoder's, so switching engines can't change the emitted QR frames.
    (If this ever fails once uUR is built, it's a real cUR-vs-ur2 discrepancy to investigate.)"""
    msg = _payload()
    native = UREncoder(ur=UR("bytes", msg), max_fragment_len=30)
    ref = _Ur2Encoder(UR("bytes", bytearray(msg)), 30)
    n = ref.fountain_encoder.seq_len()

    assert native.fountain_encoder.seq_len() == n
    assert [native.next_part() for _ in range(n)] == [ref.next_part() for _ in range(n)]


def test_native_cur_decodes_pure_python_parts():
    """The native decoder consumes parts produced by the pure-Python encoder (the real scan
    case: any conformant BC-UR source), reassembling the original CBOR."""
    msg = _payload()
    ref = _Ur2Encoder(UR("bytes", bytearray(msg)), 30)
    parts = [ref.next_part() for _ in range(ref.fountain_encoder.seq_len())]

    dec = URDecoder()
    for p in parts:
        dec.receive_part(p)
    assert dec.is_complete() is True
    assert bytes(dec.result_message().cbor) == bytes(msg)
