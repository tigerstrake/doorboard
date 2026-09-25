"""deploy/pi-door/doorboard.env.example must be safe to hand to systemd as-is.

``deploy/pi-door/install-systemd`` loads the installed copy through ``EnvironmentFile=``,
and systemd does **not** treat a trailing ``# comment`` as a comment — it keeps it in the
value. Verified on the door Pi: ``FOO=bar   # trailing comment`` arrives as
``bar   # trailing comment``. On this template that would have set ``ESP32_TRANSPORT`` to
``"uart   # uart | udp | mock"`` (silently disabling the controller link) and made
door-api die on ``int("4096 # bounded durable queue …")``. ``install-home-layout``
bash-sources the same file, where ``#`` really is a comment, which is exactly how the trap
stays invisible until the other installer is used.

So every comment in the Pi template sits on its own line — including on commented-out
assignments, because an operator uncomments those verbatim.

The same file is also the trust boundary in written form (ARCHITECTURE.md §Trust): the
door Pi gets limited-scope credentials only, so NUC-only keys must never appear in it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PI_TEMPLATE = REPO_ROOT / "deploy/pi-door/doorboard.env.example"
ROOT_TEMPLATE = REPO_ROOT / ".env.example"

# `KEY=value`, live or commented out (`# KEY=value`), capturing the value.
ASSIGNMENT = re.compile(r"^(?:#\s*)?([A-Z][A-Z0-9_]*)=(.*)$")
# A value followed by whitespace and `#`. A `#` with no space before it (a URL fragment,
# a generated secret) is a legitimate value and must not trip this.
TRAILING_COMMENT = re.compile(r"^([^#]*?)\s+#")

# The public relay (ADR-0043) is live: a replacement Pi built from the template without
# these fails scripts/enrollment-qr's preflight with "relay is not configured".
RELAY_KEYS = (
    "VISIOND_RELAY_BASE_URL",
    "VISIOND_RELAY_DEVICE_TOKEN",
    "VISIOND_RELAY_PUBLIC_URL",
    "DOOR_API_VISITOR_RELAY_BASE_URL",
    "DOOR_API_VISITOR_RELAY_DEVICE_TOKEN",
    "DOOR_API_VISITOR_RELAY_PUBLIC_URL",
)

# NUC-only credentials and settings that must never be copied onto the door.
NUC_ONLY_KEYS = (
    "HOME_ASSISTANT_URL",
    "HOME_ASSISTANT_TOKEN",
    "POSTGRES_DSN",
    "POSTGRES_PASSWORD",
    "CONTROL_PLANE_ADMIN_TOKEN",
    "CONTROL_PLANE_ENROLLMENT_KEY",
    "TELEGRAM_BOT_TOKEN",
    "OPENAI_API_KEY",
)

# Read by nothing (audit H1): a setting you can write and have silently ignored is worse
# than one that does not exist.
DEAD_KEYS = ("SINGLE_CAMERA_MODE", "DOORBOARD_ENV")


def _assignments(path: Path) -> list[tuple[int, str, str, bool]]:
    """(line number, key, value, is_live) for every assignment, commented ones included."""
    found: list[tuple[int, str, str, bool]] = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        match = ASSIGNMENT.match(line)
        if match:
            found.append((number, match.group(1), match.group(2), not line.startswith("#")))
    return found


def _live_keys(path: Path) -> set[str]:
    return {key for _n, key, _v, live in _assignments(path) if live}


def _all_keys(path: Path) -> set[str]:
    return {key for _n, key, _v, _live in _assignments(path)}


def test_the_template_is_where_we_think_and_non_trivial() -> None:
    # Guards the guard: a moved or gutted file must fail loudly, not pass on an empty set.
    assert PI_TEMPLATE.exists(), f"Pi template moved: {PI_TEMPLATE}"
    assert len(_live_keys(PI_TEMPLATE)) > 30, "Pi template parsed as near-empty"


def test_no_value_line_carries_a_trailing_comment() -> None:
    offenders = [
        f"  doorboard.env.example:{number}  {key}={value.strip()[:80]}"
        for number, key, value, _live in _assignments(PI_TEMPLATE)
        if TRAILING_COMMENT.match(value) or value.lstrip().startswith("#")
    ]
    assert not offenders, (
        "systemd's EnvironmentFile= keeps everything after '=', comment included, so these "
        "values would contain their own comment text. Put the comment on the line above:\n"
        + "\n".join(offenders)
    )


def test_the_relay_settings_are_live_lines_not_comments() -> None:
    missing = sorted(set(RELAY_KEYS) - _live_keys(PI_TEMPLATE))
    assert not missing, (
        "the public relay needs these on every door; a Pi rebuilt from the template "
        f"without them fails scripts/enrollment-qr preflight: {missing}"
    )


def test_no_nuc_only_credential_appears_in_the_pi_template() -> None:
    leaked = sorted(set(NUC_ONLY_KEYS) & _all_keys(PI_TEMPLATE))
    assert not leaked, f"NUC-only keys must never be on the door Pi (ARCHITECTURE §Trust): {leaked}"


def test_dead_settings_are_gone_from_both_templates() -> None:
    for path in (PI_TEMPLATE, ROOT_TEMPLATE):
        dead = sorted(set(DEAD_KEYS) & _all_keys(path))
        assert not dead, f"{path.name} documents settings nothing reads: {dead}"


def test_home_assistant_stays_under_the_nuc_only_fence_in_the_root_template() -> None:
    # The root template is per-host; the two HA lines must sit right under the comment
    # that says they never go to the Pi, so nobody copies a block without its warning.
    lines = ROOT_TEMPLATE.read_text().splitlines()
    url_index = next(i for i, line in enumerate(lines) if line.startswith("HOME_ASSISTANT_URL="))
    preceding = "\n".join(lines[max(0, url_index - 6) : url_index])
    assert "NUC ONLY" in preceding, "HOME_ASSISTANT_* lost their NUC-only comment"
    assert "Pi" in preceding, "the comment must say these never go to the Pi"
