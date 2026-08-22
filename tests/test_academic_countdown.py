"""ADR-0039: days until the next academic milestone, from a table not a scraper.

The interesting cases are all "what does it do when the data is wrong or old",
because the failure that matters is a wallboard confidently counting down to a date
that has already passed.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps/wallboard-worker/src"))

from wallboard_worker.jobs import load_academic_milestones  # noqa: E402

SHIPPED_TABLE = REPO / "integrations/academic-calendar/stanford-2026-2027.json"


def _write(tmp_path: Path, milestones: list[dict], source: str = "test") -> Path:
    path = tmp_path / "cal.json"
    path.write_text(json.dumps({"source": source, "milestones": milestones}))
    return path


# --- the shipped table ------------------------------------------------------


def test_the_shipped_table_parses_and_is_undergrad_scoped():
    loaded = load_academic_milestones(SHIPPED_TABLE, today=date(2026, 8, 22))
    assert loaded is not None
    milestones, source = loaded
    assert "Stanford" in source
    labels = " ".join(m["label"] for m in milestones).lower()
    # The owner asked for "only the important stuff" — planning dates, not
    # administrative ones.
    for administrative in ("grades due", "conferral", "recommending list"):
        assert administrative not in labels


def test_the_shipped_table_is_in_order_and_dated_sanely():
    raw = json.loads(SHIPPED_TABLE.read_text())
    dates = [date.fromisoformat(m["date"]) for m in raw["milestones"]]
    assert dates == sorted(dates), "table should read chronologically"
    assert all(d.year in (2026, 2027) for d in dates)


def test_finals_come_after_the_last_day_of_classes():
    """A table where finals precede the last class is a transcription error."""
    raw = json.loads(SHIPPED_TABLE.read_text())
    by_label = {m["label"]: date.fromisoformat(m["date"]) for m in raw["milestones"]}
    assert by_label["Autumn finals begin"] > by_label["Last day of autumn classes"]
    assert by_label["Autumn finals end"] >= by_label["Autumn finals begin"]


# --- counting ---------------------------------------------------------------


def test_counts_whole_days_to_the_soonest_future_date(tmp_path):
    path = _write(
        tmp_path,
        [
            {"label": "Later", "date": "2026-10-01", "kind": "term_start"},
            {"label": "Sooner", "date": "2026-09-01", "kind": "finals"},
        ],
    )
    milestones, _ = load_academic_milestones(path, today=date(2026, 8, 22))
    assert milestones[0]["label"] == "Sooner"
    assert milestones[0]["days_until"] == 10


def test_today_counts_as_zero_not_as_past(tmp_path):
    """Finals starting today should read "today", not vanish."""
    path = _write(tmp_path, [{"label": "Finals", "date": "2026-12-07", "kind": "finals"}])
    milestones, _ = load_academic_milestones(path, today=date(2026, 12, 7))
    assert milestones[0]["days_until"] == 0


def test_past_dates_are_dropped(tmp_path):
    path = _write(
        tmp_path,
        [
            {"label": "Gone", "date": "2026-01-01", "kind": "term_start"},
            {"label": "Ahead", "date": "2026-12-01", "kind": "finals"},
        ],
    )
    milestones, _ = load_academic_milestones(path, today=date(2026, 8, 22))
    assert [m["label"] for m in milestones] == ["Ahead"]


# --- degradation: the cases that keep a wrong number off the screen ---------


def test_an_exhausted_table_publishes_nothing(tmp_path):
    """The year has ended and nobody refreshed the table.

    Returning None means the job publishes no event at all, so the tile shows its
    own empty state rather than a countdown to a date in the past.
    """
    path = _write(tmp_path, [{"label": "Old", "date": "2026-01-01", "kind": "finals"}])
    assert load_academic_milestones(path, today=date(2027, 6, 1)) is None


def test_a_missing_table_publishes_nothing(tmp_path):
    assert load_academic_milestones(tmp_path / "nope.json", today=date(2026, 8, 22)) is None


def test_unreadable_json_publishes_nothing(tmp_path):
    path = tmp_path / "cal.json"
    path.write_text("{ this is not json")
    assert load_academic_milestones(path, today=date(2026, 8, 22)) is None


def test_one_malformed_row_does_not_discard_the_year(tmp_path):
    """A typo in one entry must not cost every other date."""
    path = _write(
        tmp_path,
        [
            {"label": "Bad date", "date": "not-a-date", "kind": "finals"},
            {"label": "No kind", "date": "2026-12-01"},
            {"label": "Fine", "date": "2026-12-02", "kind": "finals"},
        ],
    )
    milestones, _ = load_academic_milestones(path, today=date(2026, 8, 22))
    assert [m["label"] for m in milestones] == ["Fine"]


def test_an_empty_milestone_list_publishes_nothing(tmp_path):
    assert load_academic_milestones(_write(tmp_path, []), today=date(2026, 8, 22)) is None


def test_source_falls_back_to_the_filename(tmp_path):
    """So a table with no source label is still identifiable on screen."""
    path = tmp_path / "mystery.json"
    path.write_text(
        json.dumps({"milestones": [{"label": "X", "date": "2026-12-01", "kind": "finals"}]})
    )
    _, source = load_academic_milestones(path, today=date(2026, 8, 22))
    assert source == "mystery.json"


# --- the payload actually validates ----------------------------------------


def test_a_loaded_milestone_satisfies_the_contract():
    from doorboard_contracts.events import AcademicMilestone, AmbientAcademicCountdownPayload

    milestones, source = load_academic_milestones(SHIPPED_TABLE, today=date(2026, 8, 22))
    payload = AmbientAcademicCountdownPayload(
        next=AcademicMilestone(**milestones[0]),
        upcoming=[AcademicMilestone(**m) for m in milestones[1:4]],
        source=source,
    )
    assert payload.next.days_until >= 0
    assert len(payload.upcoming) == 3


@pytest.mark.parametrize("kind", ["term_start", "classes_end", "finals", "break", "commencement"])
def test_every_kind_in_the_shipped_table_is_a_contract_kind(kind):
    """The contract's Literal and the table's vocabulary must agree."""
    from doorboard_contracts.events import AcademicMilestone

    AcademicMilestone(label="x", date=date(2026, 12, 1), days_until=1, kind=kind)


def test_the_shipped_table_uses_no_kind_the_contract_rejects():
    import typing

    from doorboard_contracts.events import AcademicMilestone

    allowed = set(typing.get_args(AcademicMilestone.model_fields["kind"].annotation))
    used = {m["kind"] for m in json.loads(SHIPPED_TABLE.read_text())["milestones"]}
    assert not (used - allowed), f"table uses kinds the contract rejects: {used - allowed}"
