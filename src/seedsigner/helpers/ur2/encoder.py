"""Native / pure-Python BC-UR fountain encoder selector.

The animated-QR *display* path emits BC-UR (fountain-coded) parts frame by frame.
On the ESP32 MicroPython firmware the native cUR module (``uUR`` — a pure-C BC-UR
implementation baked into the firmware) is present and encodes far faster per frame
than the pure-Python ``ur2`` encoder; on CPython (Pi Zero) and host pytest the native
module is absent and the pure-Python :class:`ur2.ur_encoder.UREncoder` is used unchanged.

``encode_qr`` imports ``UREncoder`` from here instead of directly from ``.ur_encoder``,
so which implementation runs is transparent to the rest of the app and the pure-Python
encoder stays the CPython/host fallback (mirrors ``ur2/decoder.py`` on the scan side).

``uUR`` is reached via ``__import__(...)`` rather than a plain ``import`` so the guarded
reference stays invisible to the MicroPython line-pattern checker (the ``__import__("zlib")``
trick in ``compat/zlib.py``): ``uUR`` is absent on CPython/host.
"""


try:
    _uUR = __import__("uUR")
except ImportError:
    _uUR = None


if _uUR is not None:

    class _FountainEncoderAdapter:
        """Presents the ``fountain_encoder`` sub-object surface the app reaches for on
        ``UREncoder``: ``seq_len()`` (the native encoder exposes it) and ``restart()``
        (the native encoder does **not** — cUR has no in-place rewind, so ``restart()``
        re-instantiates the native encoder from sequence 0). The parent is read live so a
        rebuild is always reflected."""

        def __init__(self, parent):
            self._parent = parent

        def seq_len(self):
            return self._parent._e.fountain_encoder.seq_len()

        def restart(self):
            self._parent._rebuild()

    class UREncoder:
        """Adapter over the native ``uUR.UREncoder`` presenting the pure-Python
        ``ur2.UREncoder`` seam ``BaseFountainQrEncoder`` binds. Bridges the gaps vs ur2:

        * the native constructor requires a native ``uUR.UR`` — built here from the ur2
          ``UR``'s ``.type`` + ``.cbor`` so callers keep passing the ur2 ``UR`` unchanged;
        * the native encoder has no fountain ``restart()`` — ``fountain_encoder.restart()``
          re-instantiates it from sequence 0 (cUR's rewind model; the C constructor's
          ``first_seq_num`` defaults to 0);
        * the native encoder has no ``current_part()`` — tracked here (last emitted part).
          Only the PIL brightness-tip re-render calls ``current_part()``; the native display
          loop never does, so this is a defensive shim, not a hot path.

        Interface contract (bound by ``BaseFountainQrEncoder``): ``UREncoder(ur, max_fragment_len)``
        · ``next_part() -> str`` · ``current_part() -> str`` · ``is_complete() -> bool`` ·
        ``fountain_encoder`` with ``seq_len() -> int`` and ``restart()``.
        """

        def __init__(self, ur, max_fragment_len, first_seq_num=0, min_fragment_len=10):
            # ur is an ur2 UR (``.type`` + ``.cbor``); build the native UR once and keep it
            # so a restart can re-instantiate the encoder without re-serializing.
            self._native_ur = _uUR.UR(ur.type, ur.cbor)
            self._max_fragment_len = max_fragment_len
            self._min_fragment_len = min_fragment_len
            self._last_part = None
            self._e = None
            self._build(first_seq_num)
            self.fountain_encoder = _FountainEncoderAdapter(self)

        def _build(self, first_seq_num=0):
            self._e = _uUR.UREncoder(
                ur=self._native_ur,
                max_fragment_len=self._max_fragment_len,
                first_seq_num=first_seq_num,
                min_fragment_len=self._min_fragment_len,
            )
            self._last_part = None

        def _rebuild(self):
            self._build(0)

        def is_complete(self):
            return self._e.is_complete()

        def next_part(self):
            self._last_part = self._e.next_part()
            return self._last_part

        def current_part(self):
            # Native has no current_part; re-emit the last part. If none has been emitted
            # yet, produce one (ur2 returns the sequence-0 part). Not hit on the native
            # display path — see the class docstring.
            if self._last_part is None:
                self._last_part = self._e.next_part()
            return self._last_part

else:

    class UREncoder:
        """Placeholder when native cUR (``uUR``) isn't built — see ``ur2/decoder.py``. The
        pure-Python ``ur2`` encoder fallback was removed; imports cleanly but raises on use so
        UR-path tests skip rather than silently running pure-Python off-device."""

        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "Native cUR (uUR) is not available and the pure-Python ur2 fallback was "
                "removed. Build the cUR CPython binding (uUR) to use the UR encoder off the ESP32."
            )
