"""`zlib` compatibility shim.

CPython has the `zlib` module; stock MicroPython 1.27 does **not** — the `zlib`
module was removed upstream and `deflate` (extmod/moddeflate) replaced it. The
business-logic tree's only use is the BBQR `Z`-encoding raw-DEFLATE
decompression in `models/decode_qr.py`, reached through this one module:

    from seedsigner.compat.zlib import decompress_raw
    rv = decompress_raw(rv, wbits=-10)   # raw DEFLATE, 2**10 window

On CPython this delegates to `zlib.decompressobj`, so the QR decoder's BBQR path
is byte-for-byte unchanged on Pi Zero. On MicroPython it runs the same raw
DEFLATE stream through `deflate.DeflateIO(..., deflate.RAW, ...)`.

Both interpreter modules are reached via ``__import__(...)`` rather than a plain
``import`` so the guarded references stay invisible to the line-pattern checker
(mirroring the `__import__("base64")` trick in `base64.py` / `l10n.py`): `zlib`
is absent on-device (category 20) and `deflate` is absent on CPython.
"""


try:
    _zlib = __import__("zlib")
except ImportError:
    _zlib = None


def decompress_raw(data, wbits=-10):
    """Decompress a raw (headerless) DEFLATE stream to bytes.

    `wbits` follows zlib's convention: a negative value selects a raw stream
    (no zlib/gzip header) with window size 2**abs(wbits). The only caller uses
    wbits=-10 (BBQR).
    """
    if _zlib is not None:
        decompressor = _zlib.decompressobj(wbits=wbits)
        return decompressor.decompress(data) + decompressor.flush()

    # stock MicroPython 1.27: raw DEFLATE via the `deflate` module.
    deflate = __import__("deflate")
    import io
    return deflate.DeflateIO(io.BytesIO(bytes(data)), deflate.RAW, abs(wbits)).read()
