"""ADR-0009 P-3: the Embedding type refuses serialization (mechanism E-2)."""

from __future__ import annotations

import json
import pickle

import pytest
from door_visiond.embedding import Embedding


def test_json_dumps_raises() -> None:
    emb = Embedding((0.1, 0.2, 0.3))
    with pytest.raises(TypeError):
        json.dumps(emb)


def test_pickle_dumps_raises() -> None:
    emb = Embedding((0.1, 0.2, 0.3))
    with pytest.raises(TypeError):
        pickle.dumps(emb)


def test_repr_and_str_reveal_no_vector_digits() -> None:
    # Distinctive values whose digits must not appear in any string form.
    emb = Embedding((987654.5, 123456.25))
    for rendered in (repr(emb), str(emb), f"{emb}"):
        assert "987654" not in rendered
        assert "123456" not in rendered
        assert rendered == "Embedding(dim=2, redacted)"


def test_embedding_is_not_iterable() -> None:
    emb = Embedding((0.1, 0.2))
    with pytest.raises(TypeError):
        list(emb)  # type: ignore[call-overload]


def test_embedding_has_no_dict_to_leak() -> None:
    emb = Embedding((0.1, 0.2))
    assert not hasattr(emb, "__dict__")


def test_expose_for_matching_is_the_only_accessor() -> None:
    values = (0.5, -0.25, 0.125)
    emb = Embedding(values)
    assert emb.expose_for_matching() == values
