"""T-502 acceptance: "Pi-side config contains only the limited upload/ingest
tokens (grep test vs denylist)."

The door Pi is stealable (ARCHITECTURE.md §2). door-sync may hold only an
ingest-scoped NUC token and a limited NAS service credential — never the
Postgres DSN, NUC/HA admin secrets, or the MQTT broker password. This test
greps the whole package source to keep it that way.
"""

from __future__ import annotations

from pathlib import Path

# Substrings that would indicate a broad/high-trust credential leaked into the
# door Pi service. Case-insensitive match against source.
DENYLIST = (
    "POSTGRES_DSN",
    "postgresql+psycopg",
    "HOME_ASSISTANT_TOKEN",
    "HOME_ASSISTANT_URL",
    "CONTROL_PLANE_ADMIN_TOKEN",
    "MQTT_PASSWORD",
    "MQTT_URL",
    "NAS_ADMIN",
    "NAS_ADMIN_TOKEN",
)

_SRC = Path(__file__).resolve().parent.parent / "src" / "door_sync"


def _iter_source() -> list[Path]:
    return sorted(_SRC.rglob("*.py"))


def test_no_broad_credentials_referenced() -> None:
    offenders: list[str] = []
    for path in _iter_source():
        text = path.read_text(encoding="utf-8").lower()
        for needle in DENYLIST:
            if needle.lower() in text:
                offenders.append(f"{path.name}: {needle}")
    assert not offenders, f"door-sync references broad credentials: {offenders}"


def test_only_limited_tokens_declared() -> None:
    """The only secret-bearing settings aliases are the ingest token and the
    NAS service target (limited scope). A local admin token for GET /queue is
    allowed (same low-trust stopgap door-media uses)."""
    settings_src = (_SRC / "settings.py").read_text(encoding="utf-8")
    assert 'alias="SYNC_INGEST_TOKEN"' in settings_src
    assert 'alias="NAS_SYNC_TARGET"' in settings_src
    # No broad-credential env aliases are wired as settings.
    for needle in DENYLIST:
        assert f'alias="{needle}"' not in settings_src
