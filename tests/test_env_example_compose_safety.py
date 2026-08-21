"""`.env.example` must be safe to copy for the NUC's compose stack.

Docker Compose's ``--env-file`` does **not** strip a trailing ``# comment`` from an
unquoted value: everything after the ``=`` becomes the value, comment included. The Pi is
unaffected because its systemd drop-ins bash-source ``.env``, where ``#`` really is a
comment — so this is a NUC-only hazard, and an invisible one.

It has already happened. The NUC's ``.env``, copied from this template, gave the
wallboard-worker::

    FOOD_HALL_IDS=# optional comma-separated hall filter, e.g. Wilbur,Stern
    FOOD_MEAL_OVERRIDE=# optional: breakfast/lunch/brunch/dinner (default: auto by LA time)
    OPENAI_API_KEY=# secret — NUC only; leave blank to use deterministic scoring

Turning ``FEATURE_FOOD=true`` on then died with ``Unknown meal '# optional: …'``, and
``food_hall_id_list()`` split the comment on its commas into three halls that do not
exist. Seventeen more compose-consumed keys carried the same pattern, including
``POSTGRES_PASSWORD`` and ``TELEGRAM_BOT_TOKEN`` — one edit each away from a password or
bot token silently containing prose.

So: any key the compose stack interpolates must carry its comment on its own line.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
COMPOSE_FILES = (
    REPO_ROOT / "infra/compose/docker-compose.yml",
    REPO_ROOT / "infra/compose/compose.dev.yml",
)

# KEY=value followed by whitespace and a '#'. Values that *start* with '#' are already
# broken and would be caught too; values containing '#' with no leading space (a URL
# fragment, a generated secret) are legitimate and must not trip this.
INLINE_COMMENT = re.compile(r"^([A-Z0-9_]+)=([^#]*?)\s+#")


def _compose_interpolated_keys() -> set[str]:
    keys: set[str] = set()
    for path in COMPOSE_FILES:
        if path.exists():
            keys |= set(re.findall(r"\$\{([A-Z0-9_]+)", path.read_text()))
    return keys


def test_the_compose_files_are_present_and_interpolate_variables() -> None:
    # Guards the guard: if the compose layout moves, this test must fail loudly rather
    # than quietly checking an empty set of keys and passing forever.
    keys = _compose_interpolated_keys()
    assert len(keys) > 20, f"only found {len(keys)} interpolated keys — has the layout moved?"


def test_no_compose_consumed_key_has_an_inline_comment() -> None:
    compose_keys = _compose_interpolated_keys()
    offenders: list[str] = []
    for number, line in enumerate(ENV_EXAMPLE.read_text().splitlines(), start=1):
        match = INLINE_COMMENT.match(line)
        if match and match.group(1) in compose_keys:
            offenders.append(f"  .env.example:{number}  {line.strip()[:96]}")

    assert not offenders, (
        "Compose's --env-file keeps everything after '=', so these values would include "
        "their own comment text. Put the comment on the line above:\n" + "\n".join(offenders)
    )


def test_every_compose_key_with_a_default_is_documented() -> None:
    # A key compose reads but the template never mentions is a setting nobody knows to
    # set — the observer location was exactly this: compose reads OBSERVER_LAT while the
    # template documented SATELLITES_OBSERVER_LAT, so following it left the satellite
    # panel computing passes for an observer at 0,0.
    documented = set(
        re.findall(r"^#?\s*([A-Z0-9_]+)=", ENV_EXAMPLE.read_text(), flags=re.MULTILINE)
    )
    missing = sorted(_compose_interpolated_keys() - documented)
    assert not missing, f"compose reads these but .env.example never mentions them: {missing}"


# ---------------------------------------------------------------------------
# Every setting a deployed service reads must be reachable from .env
# ---------------------------------------------------------------------------
#
# Two bugs of this exact shape were live at once:
#
#   * `WALLBOARD_AIRCRAFT_INTERVAL_S` was never passed through, so the aircraft poll was
#     pinned to its 30 s default. That burns OpenSky's anonymous daily quota in minutes,
#     after which every poll answers 429 — and the wallboard rendered that as "No nearby
#     aircraft" over a Bay Area full of them.
#   * `OPENSKY_CLIENT_ID`/`_SECRET` *were* passed, onto `OPENSKY_USERNAME`/`PASSWORD` — the
#     basic-auth names from before the provider moved to OAuth2. So the fix for the rate
#     limit could be filled in correctly and still do nothing.
#
# 22 of the worker's 47 settings were unreachable. A setting you can write in .env and have
# silently ignored is worse than one that does not exist.

WORKER_SETTINGS = REPO_ROOT / "apps/wallboard-worker/src/wallboard_worker/settings.py"


def _worker_aliases() -> set[str]:
    return set(re.findall(r'alias="([A-Z0-9_]+)"', WORKER_SETTINGS.read_text()))


def _compose_env_keys() -> set[str]:
    """Every key the compose stack puts in a container's environment.

    Includes hardcoded values (`CONTROL_PLANE_URL: http://…`), which are deliberately not
    operator-tunable — the point is only that the setting *arrives*.
    """
    keys: set[str] = set()
    for path in COMPOSE_FILES:
        if path.exists():
            keys |= set(re.findall(r"^\s{6}([A-Z0-9_]+):", path.read_text(), re.M))
    return keys


def test_the_worker_settings_module_is_where_we_think() -> None:
    # Guards the guard: a moved file must fail loudly, not check an empty set forever.
    assert WORKER_SETTINGS.exists()
    assert len(_worker_aliases()) > 30


def test_every_worker_setting_reaches_the_container() -> None:
    missing = sorted(_worker_aliases() - _compose_env_keys())
    assert not missing, (
        "wallboard-worker reads these, and the compose stack never passes them — so setting "
        "them in .env does nothing:\n  " + "\n  ".join(missing)
    )


def test_the_worker_starts_with_every_compose_default_applied() -> None:
    """Reachable is not the same as loadable.

    The previous test only proved each setting *arrives*. Wiring 20 of them through then
    crash-looped the worker on startup: `BIRDNET_SPECIES_FILTER` and `SATELLITES_WATCHLIST` are
    `list[str]`, pydantic-settings JSON-decodes complex types at the source *before* any
    validator runs, and the compose default is an empty string — which is not valid JSON.

    So this constructs Settings() with exactly the environment compose produces, defaults and
    all. An unloadable default is a crash loop, and a crash loop is worse than a wrong value.
    """
    import re as _re
    import subprocess
    import sys

    compose = (REPO_ROOT / "infra/compose/docker-compose.yml").read_text()
    env: dict[str, str] = {}
    # KEY: ${VAR:-default} — take the default, i.e. an operator who set nothing at all.
    for key, _var, default in _re.findall(
        r"^\s{6}([A-Z0-9_]+):\s*\$\{([A-Z0-9_]+):-([^}]*)\}", compose, _re.M
    ):
        env[key] = default
    assert len(env) > 30, "no compose defaults found — has the file layout changed?"

    # A token is required when any job is enabled; supply one so this tests the *parsing*.
    env.setdefault("WALLBOARD_WORKER_INGEST_TOKEN", "tok_for_settings_parse")

    script = (
        "from wallboard_worker.settings import Settings; "
        "s = Settings(); "
        "print(s.satellites_watchlist, s.birdnet_species_filter)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", **env},
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        "wallboard-worker cannot load the settings the compose stack gives it:\n"
        + result.stderr[-1500:]
    )


def test_every_compose_default_matches_the_setting_it_stands_in_for() -> None:
    """A compose default must mean what the code's default means.

    T-331 wired 22 settings through by generating the compose block from the Settings module —
    and the extractor's regex quietly failed on any default that was not a simple scalar. So
    `WALLBOARD_WORKER_HEARTBEAT_PATH` shipped as an empty string instead of
    `/tmp/wallboard-worker-heartbeat`, `Path("")` resolved to `.`, and the heartbeat write hit
    `IsADirectoryError` on every tick — an unhealthy container, minutes after deploy.

    Two settings are exempt: their validators treat an empty string as "use the default"
    deliberately, so a human can leave the line blank in .env.
    """
    import re as _re

    from wallboard_worker.settings import Settings

    # `list[str]` fields whose validators map "" to the documented default on purpose.
    EMPTY_MEANS_DEFAULT = {"BIRDNET_SPECIES_FILTER", "SATELLITES_WATCHLIST"}

    compose = (REPO_ROOT / "infra/compose/docker-compose.yml").read_text()
    alias_to_field = {f.alias: name for name, f in Settings.model_fields.items() if f.alias}

    mismatched: list[str] = []
    for _key, var, composed in _re.findall(
        r"^\s{6}([A-Z0-9_]+):\s*\$\{([A-Z0-9_]+):-([^}]*)\}", compose, _re.M
    ):
        name = alias_to_field.get(var)
        if name is None or var in EMPTY_MEANS_DEFAULT:
            continue
        field = Settings.model_fields[name]
        if field.default_factory is not None:
            real = str(field.default_factory())  # type: ignore[call-arg]
        elif repr(field.default) != "PydanticUndefined":
            real = str(field.default)
        else:
            continue
        if composed.strip().lower() == real.strip().lower():
            continue
        try:
            if float(composed) == float(real):
                continue
        except ValueError:
            pass
        mismatched.append(f"  {var}: compose says {composed!r}, the code says {real!r}")

    assert not mismatched, "compose defaults disagree with the code's:\n" + "\n".join(mismatched)
