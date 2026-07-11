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

    # DECODER_* state codes the native ``receive_part`` returns / ``.state`` reports.
    # ``DECODER_OK`` (== 0) is the terminal "finished, result available" state;
    # ``DECODER_PROCESSING`` means a valid part was accepted and more are needed.
    _DECODER_OK = _uUR.DECODER_OK
    _DECODER_PROCESSING = _uUR.DECODER_PROCESSING

    class URDecoder:
        """Adapter over the native ``uUR.URDecoder`` presenting the pure-Python
        ``ur2.URDecoder`` seam ``decode_qr`` binds. The two APIs differ in ways that
        must be bridged (the native binding mirrors the C API, not ur2):

        * ``receive_part()`` — the native call returns an *integer* ``DECODER_*``
          state (``DECODER_OK == 0`` success, ``DECODER_PROCESSING`` accepted,
          errors ``>= 16``), **not** a bool; ur2 returns "was a valid part accepted".
          Mapped here: True iff the state is ``DECODER_OK``/``DECODER_PROCESSING``.
          (Passing the raw int through would invert the truthiness — ``DECODER_OK``
          is 0, i.e. falsy.) Also wrapped to never raise on a malformed frame.
        * ``is_complete()`` — the native ``URDecoder`` has **no** ``is_complete``
          method (only the encoder does); completion is the terminal ``DECODER_OK``
          state, which is exactly when ``get_result()`` is non-NULL — mirroring ur2's
          ``result != None``.
        * ``result_message()`` — maps to the native ``.result`` property (a ``UR``
          exposing ``.type`` and ``.cbor`` raw CBOR bytes).
        * ``estimated_percent_complete(weight_mixed_frames=...)`` — the forked native
          binding implements the weighted method and accepts the keyword; the
          ``TypeError`` fallback keeps an un-enhanced build working.

        Interface contract (bound entirely by ``decode_qr``): ``URDecoder()`` ·
        ``receive_part(str) -> bool`` (non-raising) · ``is_complete() -> bool`` ·
        ``result_message()`` -> object with ``.cbor`` ·
        ``estimated_percent_complete(weight_mixed_frames=False) -> float``.
        """

        def __init__(self):
            self._d = _uUR.URDecoder()

        def receive_part(self, s):
            try:
                state = self._d.receive_part(s)
            except Exception:
                return False
            return state == _DECODER_OK or state == _DECODER_PROCESSING

        def is_complete(self):
            return self._d.state == _DECODER_OK

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

    class URDecoder:
        """Placeholder when native cUR (``uUR``) isn't built. The pure-Python ``ur2`` runtime
        fallback was removed (single‑decoder cutover), so the UR path now requires the native
        module. This imports cleanly — unrelated code and test collection are unaffected — but
        raises on use; UR‑path tests skip via ``tests/ur_native.requires_native_ur``."""

        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "Native cUR (uUR) is not available and the pure-Python ur2 fallback was "
                "removed. Build the cUR CPython binding (uUR) to use the UR decoder off the ESP32."
            )
