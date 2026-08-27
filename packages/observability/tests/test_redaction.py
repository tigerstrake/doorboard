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
    redact_text,
    redact_value,
)


def _record(msg: object, args: object = ()) -> logging.LogRecord:
    return logging.LogRecord(
        name="door_visiond.matcher",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,  # type: ignore[arg-type]
        exc_info=None,
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


# -- message-string scanning (the f-string hole) ----------------------------


def test_redact_text_scrubs_a_number_sequence() -> None:
    text = "vec=" + str(list(range(MAX_FLOAT_SEQUENCE + 4)))
    out = redact_text(text)
    assert REDACTED in out
    assert "1, 2, 3" not in out


def test_redact_text_scrubs_a_long_opaque_blob() -> None:
    # A 65+ char space-free run of base64 chars is redacted whole. Because `=`/`_` are in the
    # base64 alphabet, an attached label (`embedding=`) is swallowed too — safe over-redaction;
    # real logs have spaces/dots that bound the run (see the next test).
    assert redact_text("embedding=" + "A" * 80) == REDACTED
    assert redact_text("published " + "A" * 80 + " ok") == "published " + REDACTED + " ok"


def test_redact_text_leaves_ordinary_messages_alone() -> None:
    text = "match_below_threshold best_score=0.83 candidates=5"
    assert redact_text(text) == text


def test_filter_scrubs_a_vector_baked_into_an_fstring_message() -> None:
    # An f-string leaves the payload in record.msg with NO args to scan — the case the
    # arg/extra redaction could not reach before.
    vec = [round(0.1 * i, 4) for i in range(MAX_FLOAT_SEQUENCE + 4)]
    record = _record(f"vec={vec}")
    assert BiometricRedactionFilter().filter(record) is True
    message = record.getMessage()
    assert REDACTED in message
    assert "0.1" not in message and "0.2" not in message


def test_filter_scrubs_a_base64_blob_in_the_message() -> None:
    # A space bounds the blob, so the label before it survives; the 96-char blob is redacted.
    record = _record("door_key " + "Zm9vYmFy" * 12)  # 96 base64 chars
    assert BiometricRedactionFilter().filter(record) is True
    assert record.getMessage() == "door_key " + REDACTED


def test_filter_still_redacts_a_vector_passed_as_a_format_arg() -> None:
    vec = [0.1] * (MAX_FLOAT_SEQUENCE + 4)
    record = _record("vec=%s", (vec,))
    assert BiometricRedactionFilter().filter(record) is True
    assert record.getMessage() == "vec=" + REDACTED
