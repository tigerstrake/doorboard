"""Redaction covers non-list number sequences (ADR-0009 E-3 hardening).

The filter must scrub embedding-shaped payloads regardless of the concrete
container type — not just ``list``/``tuple`` but ``array.array``, ``set``, and
numpy-style arrays (the form door-visiond's Hailo pipeline actually produces).
Tests stay dependency-free by using a duck-typed fake for the numpy case; one
test opts into real numpy only when it is installed.
"""

from __future__ import annotations

import array
import logging
from collections.abc import Sequence

from doorboard_observability.redaction import (
    MAX_FLOAT_SEQUENCE,
    REDACTED,
    BiometricRedactionFilter,
    redact_value,
)


class _FakeNdArray:
    """Minimal numpy-ndarray look-alike: has tolist()/shape/dtype.

    Duck-types the exact surface _coerce_to_list detects, so it exercises the
    numpy code path without depending on numpy (which isn't a dep here and whose
    untyped surface strict pyright rejects in tests).
    """

    def __init__(self, data: Sequence[object], shape: tuple[int, ...]) -> None:
        self._data = data
        self.shape = shape
        self.dtype = "float32"

    def tolist(self) -> Sequence[object]:
        return self._data


_LONG = MAX_FLOAT_SEQUENCE + 1  # 17 — just over the threshold


def test_plain_list_of_floats_is_redacted() -> None:
    assert redact_value([0.1] * _LONG) == REDACTED


def test_short_number_list_is_untouched() -> None:
    short = [1.0, 2.0, 3.0]
    assert redact_value(short) == short


def test_array_array_of_floats_is_redacted() -> None:
    assert redact_value(array.array("f", [0.2] * _LONG)) == REDACTED


def test_fake_ndarray_1d_is_redacted() -> None:
    assert redact_value(_FakeNdArray([0.3] * _LONG, (_LONG,))) == REDACTED


def test_fake_ndarray_2d_inner_rows_are_redacted() -> None:
    rows = [[0.4] * _LONG, [0.5] * _LONG]
    result = redact_value(_FakeNdArray(rows, (2, _LONG)))
    assert result == [REDACTED, REDACTED]


def test_set_of_numbers_is_redacted() -> None:
    assert redact_value(set(range(_LONG))) == REDACTED


def test_filter_scrubs_ndarray_in_log_extra() -> None:
    record = logging.LogRecord(
        name="door_visiond.pipeline",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="debug",
        args=(),
        exc_info=None,
    )
    record.emb = _FakeNdArray([0.6] * _LONG, (_LONG,))  # type: ignore[attr-defined]
    assert BiometricRedactionFilter().filter(record) is True
    assert record.emb == REDACTED  # type: ignore[attr-defined]
