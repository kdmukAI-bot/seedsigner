"""Native / pure-Python BC-UR fountain decoder selector.

Live scanning of animated BC-UR (fountain-coded) QRs runs its per-frame
ingestion through this decoder. On the ESP32 MicroPython firmware the native
cUR module (``uUR`` — a pure-C BC-UR implementation baked into the firmware) is
present and is dramatically faster per frame than the pure-Python ``ur2``
decoder; on CPython (Pi Zero) and host pytest the native module is absent and
the pure-Python :class:`ur2.ur_decoder.URDecoder` is used unchanged.

``decode_qr`` imports ``URDecoder`` from here instead of directly from
``.ur_decoder``, so which implementation is used is transparent to the rest of
the app and the pure-Python decoder stays the CPython/host fallback.

``uUR`` is reached via ``__import__(...)`` rather than a plain ``import`` so the
guarded reference stays invisible to the MicroPython line-pattern checker
(mirroring the ``__import__("zlib")`` trick in ``compat/zlib.py``): ``uUR`` is
absent on CPython/host.
"""


try:
    _uUR = __import__("uUR")
except ImportError:
    _uUR = None


if _uUR is not None:

    class URDecoder:
        """Adapter over the native ``uUR.URDecoder`` presenting the same seam
        the pure-Python ``ur2.URDecoder`` gives ``decode_qr``. It bridges three
        small API gaps between the two:

        * ``result_message()`` maps to the native ``.result`` property (a ``UR``
          object exposing ``.type`` and ``.cbor`` raw CBOR bytes);
        * ``estimated_percent_complete(weight_mixed_frames=...)``: the native
          binding implements only the reference estimate and takes no keyword,
          so the argument is swallowed — with a ``TypeError`` fallback so a
          forked build that *does* accept the flag still works;
        * ``receive_part()``: the native binding raises on a malformed frame,
          whereas the pure-Python one swallows all errors and returns ``False``,
          so it is wrapped to never raise.

        The interface contract (bound entirely by ``decode_qr``): ``URDecoder()``
        · ``receive_part(str) -> bool`` (non-raising) · ``is_complete() -> bool``
        · ``result_message()`` -> object with ``.cbor`` ·
        ``estimated_percent_complete(weight_mixed_frames=False) -> float``.
        """

        def __init__(self):
            self._d = _uUR.URDecoder()

        def receive_part(self, s):
            try:
                return self._d.receive_part(s)
            except Exception:
                return False

        def is_complete(self):
            return self._d.is_complete()

        def result_message(self):
            return self._d.result

        def estimated_percent_complete(self, weight_mixed_frames=False):
            try:
                return self._d.estimated_percent_complete(
                    weight_mixed_frames=weight_mixed_frames
                )
            except TypeError:
                return self._d.estimated_percent_complete()

else:
    from .ur_decoder import URDecoder  # noqa: F401
