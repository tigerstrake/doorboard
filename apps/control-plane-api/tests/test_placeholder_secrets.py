"""The control plane refuses to start on the template's placeholder credentials.

`.env.example` ships `POSTGRES_PASSWORD=CHANGE_ME` and six MQTT passwords the same way, which
is the right thing for a template and a loaded gun for a deployment. The door's NUC was found
with a *duplicate* `POSTGRES_PASSWORD` — a dead `CHANGE_ME` sitting above the real secret,
working only because compose takes the last occurrence. Reorder or delete the lower line and
the whole stack would have come up on a password published in the repo, with nothing to
indicate anything had changed.

A service that will not boot is a bad afternoon. A service quietly running on a known-published
credential is a different category of problem, so this fails closed.
"""

from __future__ import annotations

import pytest
from control_plane_api.settings import (
    Settings,
    _check_no_placeholder_secrets,
    _dsn_password,
    _is_placeholder,
)

REAL_DSN = "postgresql+psycopg://doorboard:4370eca8966ee1b2@postgres:5432/doorboard"
PLACEHOLDER_DSN = "postgresql+psycopg://doorboard:CHANGE_ME@postgres:5432/doorboard"


def _settings(**overrides: object) -> Settings:
    """Build Settings by *alias*.

    The fields carry aliases (POSTGRES_DSN, MQTT_PASSWORD, …), so passing the python names is
    silently ignored and you get the defaults — which is exactly how the first version of
    these tests managed to "pass" against a guard that was never being handed the value under
    test.
    """
    base: dict[str, object] = {"POSTGRES_DSN": REAL_DSN}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_a_real_configuration_starts() -> None:
    _check_no_placeholder_secrets(_settings())


@pytest.mark.parametrize(
    "placeholder", ["CHANGE_ME", "change_me", "changeme", "  CHANGE_ME  ", "change-me", "TODO"]
)
def test_a_placeholder_database_password_refuses_to_start(placeholder: str) -> None:
    dsn = f"postgresql+psycopg://doorboard:{placeholder.strip()}@postgres:5432/doorboard"
    with pytest.raises(ValueError, match="placeholder credentials"):
        _check_no_placeholder_secrets(_settings(POSTGRES_DSN=dsn))


def test_it_names_every_offender_so_one_fix_does_not_hide_the_next() -> None:
    with pytest.raises(ValueError) as excinfo:
        _check_no_placeholder_secrets(
            _settings(
                POSTGRES_DSN=PLACEHOLDER_DSN,
                MQTT_PASSWORD="CHANGE_ME",
                CONTROL_PLANE_ADMIN_TOKEN="changeme",
            )
        )
    message = str(excinfo.value)
    assert "POSTGRES_DSN" in message
    assert "MQTT_PASSWORD" in message
    assert "CONTROL_PLANE_ADMIN_TOKEN" in message
    # And it says what to do about it.
    assert "openssl rand" in message


def test_empty_secrets_are_left_alone() -> None:
    # Unset is not a placeholder: MQTT fan-out and notifications are optional by design and
    # disable themselves when blank (ARCHITECTURE.md §10). Failing on empty would break dev
    # and CI, which is precisely the outcome that gets a safety check deleted.
    _check_no_placeholder_secrets(_settings(MQTT_PASSWORD="", CONTROL_PLANE_ADMIN_TOKEN=""))


def test_a_malformed_dsn_does_not_crash_the_check() -> None:
    # Missing a placeholder is better than refusing to boot for an unrelated parsing reason.
    for dsn in ("nonsense", "postgresql://localhost/db", "", "://@"):
        assert _dsn_password(dsn) == "" or isinstance(_dsn_password(dsn), str)
        _check_no_placeholder_secrets(_settings(POSTGRES_DSN=dsn))


def test_a_password_that_merely_contains_a_placeholder_word_is_fine() -> None:
    # "change_me_now_9f2c" is a real (if unwise) secret, not the template's value. This check
    # must not start rejecting legitimate credentials, or it will be turned off.
    assert not _is_placeholder("change_me_now_9f2c")
    _check_no_placeholder_secrets(
        _settings(POSTGRES_DSN=REAL_DSN.replace("4370eca8966ee1b2", "change_me_now_9f2c"))
    )
