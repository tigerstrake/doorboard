"""T-502 acceptance: "Pi-side config contains only the limited upload/ingest
tokens (grep test vs denylist)."

The door Pi is stealable (ARCHITECTURE.md §2). It may hold only its own
low-trust local admin tokens, an ingest-scoped NUC token and a limited NAS
service credential -- never the Postgres DSN or password, NUC/HA admin secrets,
the Telegram bot token, third-party API keys, or the MQTT broker passwords.

The denylist is *derived*: every secret-shaped variable the NUC side declares
in ``.env.example`` or reads in ``infra/compose`` (``PASSWORD``, ``ADMIN_TOKEN``,
``BOT_TOKEN``, ``API_KEY``, ``DSN``), minus an explicit allowlist of what the Pi
legitimately holds. A hand-written list missed most of the NUC's real names
(POSTGRES_PASSWORD, TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, ...) and only ever
looked at this one package. The scan covers the Pi deploy tree and every
door service's source. Read-only.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

_SECRET_SHAPED = re.compile(r"^[A-Z0-9_]*(PASSWORD|ADMIN_TOKEN|BOT_TOKEN|API_KEY|DSN)[A-Z0-9_]*$")
_ENV_ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]+)=")
_COMPOSE_REFERENCE = re.compile(r"\$\{?([A-Z][A-Z0-9_]+)")

# What the door Pi is allowed to hold (deploy/pi-door/doorboard.env.example):
# per-service local admin tokens for its own loopback routes, and the scoped
# MQTT user door-api publishes with. Adding a name here is a trust-model
# decision (ARCHITECTURE.md §2), not a convenience.
PI_ALLOWLIST = frozenset(
    {
        "DOOR_API_SOCIAL_ADMIN_TOKEN",
        "DOOR_VISIOND_ADMIN_TOKEN",
        "DOOR_MEDIA_ADMIN_TOKEN",
        "DOOR_SYNC_ADMIN_TOKEN",
        "DOOR_API_MQTT_PASSWORD",
    }
)

# Broad credentials that are not secret-*shaped* by name but must not appear
# either: the DSN scheme, HA admin config, the broker URL, NAS admin.
STATIC_DENYLIST = (
    "postgresql+psycopg",
    "HOME_ASSISTANT_TOKEN",
    "HOME_ASSISTANT_URL",
    "MQTT_URL",
    "NAS_ADMIN",
    "NAS_ADMIN_TOKEN",
)

# Names the derivation must find, so it cannot silently degrade to an empty list.
MUST_DERIVE = {
    "POSTGRES_DSN",
    "POSTGRES_PASSWORD",
    "CONTROL_PLANE_ADMIN_TOKEN",
    "CONTROL_PLANE_DOOR_API_ADMIN_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "OPENAI_API_KEY",
    "MQTT_PASSWORD",
}

SCAN_TARGETS = (
    ("deploy/pi-door", "*"),
    ("apps/door-api/src", "*.py"),
    ("apps/door-visiond/src", "*.py"),
    ("apps/door-media/src", "*.py"),
    ("apps/door-sync/src", "*.py"),
    ("apps/door-voice/src", "*.py"),
)


def derived_denylist() -> frozenset[str]:
    names: set[str] = set()
    for line in (REPO / ".env.example").read_text(encoding="utf-8").splitlines():
        match = _ENV_ASSIGNMENT.match(line.strip())
        if match:
            names.add(match.group(1))
    for path in (REPO / "infra" / "compose").rglob("*"):
        if path.is_file() and path.suffix in {".yml", ".yaml", ".sh", ".env", ".conf", ""}:
            names.update(
                _COMPOSE_REFERENCE.findall(path.read_text(encoding="utf-8", errors="replace"))
            )
    return frozenset(n for n in names if _SECRET_SHAPED.match(n)) - PI_ALLOWLIST


def _scan_files() -> list[Path]:
    files: list[Path] = []
    for rel, pattern in SCAN_TARGETS:
        root = REPO / rel
        assert root.exists(), f"scan target missing: {root}"
        files.extend(p for p in sorted(root.rglob(pattern)) if p.is_file())
    return files


def test_the_denylist_is_derived_from_the_nuc_side_config() -> None:
    denylist = derived_denylist()
    missing = MUST_DERIVE - denylist
    assert not missing, f"derivation lost known high-trust names: {sorted(missing)}"
    assert not (denylist & PI_ALLOWLIST)


def test_no_broad_credentials_referenced_on_the_pi() -> None:
    """Whole-word and case-sensitive: the names are environment variables, which
    are uppercase by convention. Word-bounded so DOOR_API_MQTT_PASSWORD (allowed)
    does not trip on MQTT_PASSWORD (denied) as a substring, and case-sensitive so
    door-api's own `mqtt_password` attribute -- the Pi-scoped broker user behind
    that allowed alias -- is not mistaken for the NUC's MQTT_PASSWORD."""
    denylist = sorted(derived_denylist() | set(STATIC_DENYLIST))
    pattern = re.compile(
        r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(n) for n in denylist) + r")(?![A-Za-z0-9_])"
    )
    offenders: list[str] = []
    for path in _scan_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in pattern.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.relative_to(REPO)}:{line_no}: {match.group(1)}")
    assert not offenders, "the door Pi references broad credentials:\n" + "\n".join(offenders)


def test_only_limited_tokens_declared() -> None:
    """The only secret-bearing settings aliases in door-sync are the ingest token,
    the NAS service target (limited scope) and the local admin token for GET /queue
    (the same low-trust stopgap door-media uses)."""
    settings_src = (REPO / "apps/door-sync/src/door_sync/settings.py").read_text(encoding="utf-8")
    assert 'alias="SYNC_INGEST_TOKEN"' in settings_src
    assert 'alias="NAS_SYNC_TARGET"' in settings_src
    for needle in sorted(derived_denylist() | set(STATIC_DENYLIST)):
        assert f'alias="{needle}"' not in settings_src, needle
