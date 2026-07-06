"""The ``Embedding`` no-serialize boundary (ADR-0009 §2, mechanism E-2).

A face embedding is the most sensitive object in the system.  This class wraps
the raw vector so that the *only* way to reach the numbers is an explicit
:meth:`Embedding.expose_for_matching` call — a grep for that name enumerates
every legitimate consumer (the matcher; the enrollment store's serialization
of *consented* vectors).

Hard guarantees enforced here (proven by test P-3):

- ``json.dumps(embedding)`` raises ``TypeError`` (no ``__json__`` / dict / list
  interface, and an explicit JSON hook that refuses).
- ``pickle.dumps(embedding)`` raises ``TypeError`` (``__reduce_ex__`` /
  ``__getstate__`` refuse; ``__slots__`` means there is no ``__dict__`` to leak).
- ``repr``/``str``/f-string render ``Embedding(dim=N, redacted)`` — never a digit
  of the vector, so accidental logging cannot leak biometric data.
- No ``__iter__``: ``list(embedding)`` / unpacking fail loudly.
"""

from __future__ import annotations

from array import array
from typing import Final, NoReturn

_LE_FLOAT32: Final[str] = "<f"  # documentation only; array handles native order


class Embedding:
    """An opaque, non-serializable face embedding vector."""

    __slots__ = ("_values",)

    def __init__(self, values: tuple[float, ...]) -> None:
        # Stored as an immutable tuple of Python floats; no public accessor
        # exists other than expose_for_matching().
        object.__setattr__(self, "_values", tuple(values))

    # ------------------------------------------------------------------
    # Construction from stored bytes (enrollment load / sentinel fixtures)
    # ------------------------------------------------------------------

    @classmethod
    def from_le_float32_bytes(cls, buf: bytes) -> Embedding:
        """Build an Embedding from a little-endian float32 byte buffer."""
        if len(buf) % 4 != 0:
            msg = "embedding byte buffer length must be a multiple of 4"
            raise ValueError(msg)
        arr = array("f")
        arr.frombytes(buf)
        # array uses native byte order; the enrollment store and this loader
        # both run on the same device, so native order round-trips exactly.
        return cls(tuple(arr))

    # ------------------------------------------------------------------
    # The single explicit exposure point
    # ------------------------------------------------------------------

    def expose_for_matching(self) -> tuple[float, ...]:
        """Return the raw vector.  The ONLY sanctioned raw accessor (E-2)."""
        return self._values

    def to_le_float32_bytes(self) -> bytes:
        """Serialize to float32 bytes for the enrollment DB (consented vectors).

        Routed through :meth:`expose_for_matching` so the exposure grep
        enumerates this consumer too (ADR-0009 §8).
        """
        return array("f", self.expose_for_matching()).tobytes()

    @property
    def dim(self) -> int:
        return len(self._values)

    # ------------------------------------------------------------------
    # Serialization refusals
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"Embedding(dim={len(self._values)}, redacted)"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        msg = "Embedding is not picklable (ADR-0009 E-2)"
        raise TypeError(msg)

    def __reduce_ex__(self, protocol: int) -> NoReturn:
        msg = "Embedding is not picklable (ADR-0009 E-2)"
        raise TypeError(msg)

    def __getstate__(self) -> NoReturn:
        msg = "Embedding is not serializable (ADR-0009 E-2)"
        raise TypeError(msg)

    def __json__(self) -> NoReturn:  # some serializers probe this hook
        msg = "Embedding is not JSON-serializable (ADR-0009 E-2)"
        raise TypeError(msg)

    def for_json(self) -> NoReturn:  # simplejson-style hook
        msg = "Embedding is not JSON-serializable (ADR-0009 E-2)"
        raise TypeError(msg)
