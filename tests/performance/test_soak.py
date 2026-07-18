from __future__ import annotations

import asyncio

from tests.performance.soak import (
    SoakConfig,
    build_soak_report,
    result_to_json,
    run_simulator_soak,
)


def test_simulator_soak_ci_profile_passes() -> None:
    result = asyncio.run(run_simulator_soak(SoakConfig.ci()))

    assert result.passed, result.failures
    assert result.counters.expected_presses > 0
    assert result.counters.observed_presses == result.counters.expected_presses
    assert result.counters.nas_outage_injected is True
    assert result.counters.nas_recovery_injected is True
    assert result.resources["final_queue_depth"] <= result.resources["queue_depth_at_recovery"]
    assert result.latency["paths"]["button_to_generic_feedback"]["count"] > 0


def test_simulator_soak_throttling_fault_fails() -> None:
    config = SoakConfig.ci()
    fault_config = SoakConfig(
        duration_s=config.duration_s,
        step_s=config.step_s,
        press_interval_s=config.press_interval_s,
        arrival_interval_s=config.arrival_interval_s,
        recording_interval_s=config.recording_interval_s,
        webrtc_churn_interval_s=config.webrtc_churn_interval_s,
        nas_outage_start_s=config.nas_outage_start_s,
        nas_outage_duration_s=config.nas_outage_duration_s,
        induce_throttling=True,
    )

    result = asyncio.run(run_simulator_soak(fault_config))

    assert not result.passed
    assert any("throttling" in failure for failure in result.failures)


def test_simulator_soak_missed_press_fault_fails() -> None:
    config = SoakConfig.ci()
    fault_config = SoakConfig(
        duration_s=config.duration_s,
        step_s=config.step_s,
        press_interval_s=config.press_interval_s,
        arrival_interval_s=config.arrival_interval_s,
        recording_interval_s=config.recording_interval_s,
        webrtc_churn_interval_s=config.webrtc_churn_interval_s,
        nas_outage_start_s=config.nas_outage_start_s,
        nas_outage_duration_s=config.nas_outage_duration_s,
        drop_press_every=3,
    )

    result = asyncio.run(run_simulator_soak(fault_config))

    assert not result.passed
    assert any("missed button presses" in failure for failure in result.failures)


def test_soak_outputs_machine_and_human_reports() -> None:
    result = asyncio.run(run_simulator_soak(SoakConfig.ci()))

    as_json = result_to_json(result)
    report = build_soak_report(result)

    assert as_json["passed"] is True
    assert as_json["measurement_mode"] == "simulator"
    assert as_json["hardware_acceptance"] is False
    assert "latency" in as_json
    assert "resources" in as_json
    assert "Doorboard simulator regression soak" in report
    assert "Not valid" in report
    assert "Queue depth graph" in report
