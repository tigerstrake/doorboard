"""Every timer-driven call to a third-party service must justify its volume.

Written after the door quietly consumed 75% of a 1,000,000/month Vercel free tier
by polling its own enrollment relay every 5 seconds, forever, with nothing to
collect: 74,187 polls, 0 enrollments. Nothing failed. Nothing logged a warning.
The first signal was a billing email saying the deployment would be paused.

The lesson is not "5 seconds was too fast". It is that a background loop calling a
metered service had no declared budget and no gate, so its cost was invisible until
it was nearly fatal. This test makes that cost explicit and reviewable.

ADDING A NEW EXTERNAL POLLER? Add it to POLLERS below. That is the point: the test
fails on an interval setting it does not recognise, so a new one cannot be added
without someone writing down what it costs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Anything above this, on a metered service, is a liability rather than a feature.
MONTHLY_BUDGET_WARN = 100_000


@dataclass(frozen=True)
class Poller:
    """One timer-driven outbound call."""

    setting: str
    target: str
    metered: bool
    """True when the far side counts requests and has a hard cap or a bill."""
    demand_gated: bool
    """True when it only runs while there is real work — the strongest defence."""
    note: str


POLLERS: tuple[Poller, ...] = (
    Poller(
        setting="VISIOND_RELAY_POLL_INTERVAL_S",
        target="public-relay on Vercel (serverless function invocations)",
        metered=True,
        demand_gated=True,
        note=(
            "ADR-0038. Gated on an open invite plus a grace window, so an idle door "
            "makes zero calls. Was ungated at 5s: ~17,000/day, 74,187 polls for 0 "
            "enrollments, 75% of the free tier."
        ),
    ),
    Poller(
        setting="WALLBOARD_AIRCRAFT_INTERVAL_S",
        target="adsb.fi (free community feed, no key — ADR-0032)",
        metered=False,
        demand_gated=False,
        note=(
            "~2,880/day, ~86,000/month, running whether or not anyone is looking at "
            "the wallboard. Not metered and within adsb.fi's stated limits, so no "
            "hard failure — but it is the largest remaining ungated external poller "
            "and the obvious next candidate for on-demand gating."
        ),
    ),
    Poller(
        setting="WALLBOARD_SATELLITE_INTERVAL_S",
        target="satellite pass provider",
        metered=False,
        demand_gated=False,
        note="Hourly. A pass prediction does not change faster than that.",
    ),
    Poller(
        setting="WALLBOARD_BIRD_INTERVAL_S",
        target="BirdNET Pi on the LAN",
        metered=False,
        demand_gated=False,
        note="Local network, not a third party. 5 minutes costs nothing.",
    ),
    Poller(
        setting="WALLBOARD_PRINTER_INTERVAL_S",
        target="OctoPrint / printer on the LAN",
        metered=False,
        demand_gated=False,
        note="Local network. Currently inert (FEATURE_PRINTER=false).",
    ),
    Poller(
        setting="WALLBOARD_FOOD_INTERVAL_S",
        target="dining menu provider",
        metered=False,
        demand_gated=False,
        note="Daily. A menu is a daily fact.",
    ),
    Poller(
        setting="WALLBOARD_COLLAGE_INTERVAL_S",
        target="bird collage image",
        metered=False,
        demand_gated=False,
        note="Daily.",
    ),
    Poller(
        setting="WALLBOARD_ACADEMIC_INTERVAL_S",
        target="a local JSON date table — no network at all (ADR-0039)",
        metered=False,
        demand_gated=True,
        note=(
            "Reads a file. There is no remote service to rate-limit, which is most "
            "of why the academic calendar is a table rather than a scraper."
        ),
    ),
    Poller(
        setting="DOOR_VISIOND_PURGE_WORKER_INTERVAL_S",
        target="door-sync on 127.0.0.1:8083",
        metered=False,
        demand_gated=True,
        note=(
            "Loopback, not a third party — and it drains an outbox, so it does "
            "nothing when the outbox is empty."
        ),
    ),
    Poller(
        setting="PRESENCE_CALENDAR_REFRESH_S",
        target="published .ics feed (Google/iCloud/Canvas — ADR-0036)",
        metered=False,
        demand_gated=False,
        note=(
            "15 minutes, ~2,880/month, and cached so presence resolution does not "
            "fetch per request. Inert until a feed URL is configured."
        ),
    ),
)

SETTINGS_MODULES = (
    REPO_ROOT / "apps/wallboard-worker/src/wallboard_worker/settings.py",
    REPO_ROOT / "apps/door-visiond/src/door_visiond/settings.py",
    REPO_ROOT / "apps/control-plane-api/src/control_plane_api/settings.py",
)


def _declared_intervals() -> dict[str, float]:
    """Every `*_INTERVAL_S` / `*_REFRESH_S` alias and its default, across services."""
    found: dict[str, float] = {}
    pattern = re.compile(
        r"(?:default=)([0-9.]+)[^)]*?alias=\"([A-Z0-9_]+(?:INTERVAL_S|REFRESH_S))\""
        r"|alias=\"([A-Z0-9_]+(?:INTERVAL_S|REFRESH_S))\"[^)]*?default=([0-9.]+)",
        re.S,
    )
    for path in SETTINGS_MODULES:
        if not path.exists():
            continue
        for m in pattern.finditer(path.read_text()):
            if m.group(2):
                found[m.group(2)] = float(m.group(1))
            elif m.group(3):
                found[m.group(3)] = float(m.group(4))
    return found


def test_the_settings_modules_are_where_we_think() -> None:
    for path in SETTINGS_MODULES:
        assert path.exists(), f"settings module moved: {path}"
    assert len(_declared_intervals()) >= 6, "interval parsing found almost nothing"


def test_every_interval_setting_is_declared_in_this_file() -> None:
    """A new external poller cannot be added without writing down its cost.

    If this fails, add an entry to POLLERS with its target, whether the far side
    meters requests, and whether it is demand-gated.
    """
    known = {p.setting for p in POLLERS}
    undeclared = sorted(set(_declared_intervals()) - known)
    assert not undeclared, (
        "these timer settings drive polling that nobody has budgeted:\n  "
        + "\n  ".join(undeclared)
        + "\n\nAdd them to POLLERS in this file."
    )


@pytest.mark.parametrize("poller", [p for p in POLLERS if p.metered], ids=lambda p: p.setting)
def test_metered_pollers_are_demand_gated(poller: Poller) -> None:
    """The actual rule.

    A metered third party is one whose limit is enforced by suspending the service.
    Rate alone cannot make that safe — an ungated loop is unbounded in time, so any
    interval eventually reaches the cap. It has to stop when there is no work.
    """
    assert poller.demand_gated, (
        f"{poller.setting} calls {poller.target}, which meters requests, on a timer "
        "with no demand gate. Slowing it down only delays the cap. Gate it on there "
        "being real work, as ADR-0038 does for the relay."
    )


@pytest.mark.parametrize("poller", POLLERS, ids=lambda p: p.setting)
def test_ungated_pollers_stay_inside_the_monthly_budget(poller: Poller) -> None:
    """An ungated poller's cost is fixed and computable, so compute it."""
    if poller.demand_gated:
        return  # gated pollers are ~0 at idle by construction
    interval = _declared_intervals().get(poller.setting)
    if interval is None:
        pytest.skip(f"{poller.setting} has no parseable default")
    monthly = (30 * 24 * 60 * 60) / interval
    assert monthly <= MONTHLY_BUDGET_WARN, (
        f"{poller.setting} at {interval}s is ~{monthly:,.0f} calls/month to "
        f"{poller.target}, over the {MONTHLY_BUDGET_WARN:,} budget. Either gate it on "
        "demand or slow it down."
    )
