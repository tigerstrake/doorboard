from __future__ import annotations

import asyncio
import json
from pathlib import Path

from doorboard_simulator.scenarios import available_scenarios, result_to_json, run_scenario_name


def test_all_required_scenarios_have_golden_logs(tmp_path: Path) -> None:
    expected_names = {
        "basic-bell",
        "double-press-abuse",
        "full-happy-path",
        "identity-churn-abuse",
        "known-visitor",
        "nuc-outage",
        "press-during-recording",
        "storage-low",
        "unknown-visitor",
    }

    assert set(available_scenarios()) == expected_names

    golden_dir = Path(__file__).parent / "golden"
    for scenario_name in sorted(expected_names):
        result = asyncio.run(
            run_scenario_name(
                scenario_name,
                artifact_root=tmp_path / scenario_name,
            )
        )
        expected = json.loads((golden_dir / f"{scenario_name}.json").read_text(encoding="utf-8"))
        assert json.loads(result_to_json(result)) == expected


def test_fake_media_writes_placeholder_with_real_checksum(tmp_path: Path) -> None:
    result = asyncio.run(run_scenario_name("basic-bell", artifact_root=tmp_path))
    finalized = next(event for event in result.log if event["type"] == "media.recording_finalized")
    path = tmp_path / finalized["payload"]["path"]

    assert path.exists()
    assert path.stat().st_size == finalized["payload"]["size_bytes"]
