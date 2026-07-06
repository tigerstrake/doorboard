from datetime import UTC, datetime, timedelta

import pytest
from doorboard_auth import (
    IssuedToken,
    ServiceTokenRecord,
    ServiceTokenScope,
    TokenExpiredError,
    TokenMalformedError,
    TokenNotFoundError,
    TokenRevokedError,
    TokenScopeError,
    authenticate,
    issue_service_token,
    parse_presented_token,
    verify_secret,
)

NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)


def _record(
    issued: IssuedToken,
    *,
    scope: ServiceTokenScope = "ingest",
    revoked_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> ServiceTokenRecord:
    return ServiceTokenRecord(
        token_id=issued.token_id,
        secret_hash=issued.secret_hash,
        scope=scope,
        door_id="primary",
        created_at=NOW,
        revoked_at=revoked_at,
        expires_at=expires_at,
    )


def test_issue_round_trips_through_parse_and_verify() -> None:
    issued = issue_service_token(scope="ingest", door_id="primary")
    parsed = parse_presented_token(issued.raw)
    assert parsed.token_id == issued.token_id
    assert verify_secret(parsed.secret, issued.secret_hash)


def test_two_issued_tokens_never_collide() -> None:
    a = issue_service_token(scope="ingest", door_id="primary")
    b = issue_service_token(scope="ingest", door_id="primary")
    assert a.token_id != b.token_id
    assert a.raw != b.raw
    assert a.secret_hash != b.secret_hash


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not-a-token",
        "dbt_only-one-part",
        "wrongprefix_abc.def",
        "dbt_.def",
        "dbt_abc.",
        "dbt_",
    ],
)
def test_malformed_tokens_rejected(raw: str) -> None:
    with pytest.raises(TokenMalformedError):
        parse_presented_token(raw)


def test_parse_handles_underscores_inside_token_id_or_secret() -> None:
    # secrets.token_urlsafe's alphabet includes "_" — a naive split("_")
    # would misparse a token_id or secret that happens to contain one.
    parsed = parse_presented_token("dbt_a_b_c.d_e_f")
    assert parsed.token_id == "a_b_c"
    assert parsed.secret == "d_e_f"


def test_authenticate_success() -> None:
    issued = issue_service_token(scope="ingest", door_id="primary")
    record = _record(issued)
    result = authenticate(
        issued.raw,
        lookup=lambda tid: record if tid == issued.token_id else None,
        required_scope="ingest",
        now=NOW,
    )
    assert result is record


def test_authenticate_unknown_token_id_rejected() -> None:
    issued = issue_service_token(scope="ingest", door_id="primary")
    with pytest.raises(TokenNotFoundError):
        authenticate(issued.raw, lookup=lambda _tid: None, required_scope="ingest", now=NOW)


def test_authenticate_tampered_secret_rejected() -> None:
    issued = issue_service_token(scope="ingest", door_id="primary")
    record = _record(issued)
    tampered = f"dbt_{issued.token_id}.not-the-real-secret"
    with pytest.raises(TokenNotFoundError):
        authenticate(
            tampered,
            lookup=lambda tid: record if tid == issued.token_id else None,
            required_scope="ingest",
            now=NOW,
        )


def test_authenticate_revoked_token_rejected() -> None:
    issued = issue_service_token(scope="ingest", door_id="primary")
    record = _record(issued, revoked_at=NOW - timedelta(seconds=1))
    with pytest.raises(TokenRevokedError):
        authenticate(
            issued.raw,
            lookup=lambda tid: record if tid == issued.token_id else None,
            required_scope="ingest",
            now=NOW,
        )


def test_authenticate_expired_token_rejected() -> None:
    issued = issue_service_token(scope="ingest", door_id="primary")
    record = _record(issued, expires_at=NOW - timedelta(seconds=1))
    with pytest.raises(TokenExpiredError):
        authenticate(
            issued.raw,
            lookup=lambda tid: record if tid == issued.token_id else None,
            required_scope="ingest",
            now=NOW,
        )


def test_authenticate_expires_at_is_inclusive_boundary() -> None:
    issued = issue_service_token(scope="ingest", door_id="primary")
    record = _record(issued, expires_at=NOW)
    with pytest.raises(TokenExpiredError):
        authenticate(
            issued.raw,
            lookup=lambda tid: record if tid == issued.token_id else None,
            required_scope="ingest",
            now=NOW,
        )


def test_authenticate_wrong_scope_rejected() -> None:
    issued = issue_service_token(scope="upload", door_id="primary")
    record = _record(issued, scope="upload")
    with pytest.raises(TokenScopeError):
        authenticate(
            issued.raw,
            lookup=lambda tid: record if tid == issued.token_id else None,
            required_scope="ingest",
            now=NOW,
        )


def test_two_tokens_for_same_door_are_independently_revocable() -> None:
    a = issue_service_token(scope="ingest", door_id="primary")
    b = issue_service_token(scope="ingest", door_id="primary")
    records = {
        a.token_id: _record(a, revoked_at=NOW - timedelta(seconds=1)),
        b.token_id: _record(b),
    }
    with pytest.raises(TokenRevokedError):
        authenticate(a.raw, lookup=records.get, required_scope="ingest", now=NOW)
    # b is unaffected by a's revocation
    assert (
        authenticate(b.raw, lookup=records.get, required_scope="ingest", now=NOW)
        is records[b.token_id]
    )
