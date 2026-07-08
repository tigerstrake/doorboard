from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from printer.provider import MockPrinterProvider, OctoPrintProvider, PrinterConfig
from wallboard_worker.jobs import run_printer_status
from wallboard_worker.settings import Settings

MOCK_OCTOPRINT_OPERATIONAL = {
    "state": "Operational",
    "job": {
        "file": {"name": None, "origin": None, "size": None, "date": None},
        "estimatedPrintTime": None,
        "filament": None,
    },
    "progress": {"completion": None, "filepos": None, "printTime": None, "printTimeLeft": None},
}

MOCK_OCTOPRINT_PRINTING = {
    "state": "Printing",
    "job": {
        "file": {"name": "test_job.gcode", "origin": "local", "size": 1024, "date": 1696250000},
        "estimatedPrintTime": 3600.0,
    },
    "progress": {"completion": 45.5, "printTime": 1800, "printTimeLeft": 1800},
}

MOCK_OCTOPRINT_PAUSED = {
    "state": "Paused",
    "job": {
        "file": {"name": "test_job.gcode", "origin": "local", "size": 1024, "date": 1696250000},
        "estimatedPrintTime": 3600.0,
    },
    "progress": {"completion": 42.0, "printTime": 1500, "printTimeLeft": 2100},
}

MOCK_OCTOPRINT_ERROR = {
    "state": "Error: M112 Emergency Stop",
    "job": {},
    "progress": {},
}


def test_mock_printer_provider_states() -> None:
    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)

    # Printing
    p1 = MockPrinterProvider("printing")
    s1 = p1.get_status(now)
    assert s1["state"] == "printing"
    assert s1["job_name"] == "benchy_0.2mm_pla.gcode"
    assert s1["progress_pct"] == 64.5
    assert s1["eta"] == now + timedelta(minutes=45)

    # Paused
    p2 = MockPrinterProvider("paused")
    s2 = p2.get_status(now)
    assert s2["state"] == "paused"
    assert s2["job_name"] == "benchy_0.2mm_pla.gcode"
    assert s2["progress_pct"] == 42.0
    assert s2["eta"] == now + timedelta(hours=1)

    # Idle
    p3 = MockPrinterProvider("idle")
    s3 = p3.get_status(now)
    assert s3["state"] == "idle"
    assert s3["job_name"] is None
    assert s3["progress_pct"] is None

    # Error
    p4 = MockPrinterProvider("error")
    s4 = p4.get_status(now)
    assert s4["state"] == "error"

    # Offline
    p5 = MockPrinterProvider("offline")
    s5 = p5.get_status(now)
    assert s5["state"] == "offline"


@patch("httpx.get")
def test_octoprint_provider_operational_idle(mock_get) -> None:
    config = PrinterConfig(octoprint_url="http://octopi.local", octoprint_api_key="key")
    provider = OctoPrintProvider(config)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_OCTOPRINT_OPERATIONAL
    mock_get.return_value = mock_resp

    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)
    res = provider.get_status(now)

    assert res["state"] == "idle"
    assert res["job_name"] is None
    assert res["progress_pct"] is None
    assert res["eta"] is None


@patch("httpx.get")
def test_octoprint_provider_printing(mock_get) -> None:
    config = PrinterConfig(octoprint_url="http://octopi.local", octoprint_api_key="key")
    provider = OctoPrintProvider(config)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_OCTOPRINT_PRINTING
    mock_get.return_value = mock_resp

    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)
    res = provider.get_status(now)

    assert res["state"] == "printing"
    assert res["job_name"] == "test_job.gcode"
    assert res["progress_pct"] == 45.5
    assert res["eta"] == now + timedelta(seconds=1800)


@patch("httpx.get")
def test_octoprint_provider_paused(mock_get) -> None:
    config = PrinterConfig(octoprint_url="http://octopi.local", octoprint_api_key="key")
    provider = OctoPrintProvider(config)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_OCTOPRINT_PAUSED
    mock_get.return_value = mock_resp

    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)
    res = provider.get_status(now)

    assert res["state"] == "paused"
    assert res["job_name"] == "test_job.gcode"
    assert res["progress_pct"] == 42.0
    assert res["eta"] == now + timedelta(seconds=2100)


@patch("httpx.get")
def test_octoprint_provider_error(mock_get) -> None:
    config = PrinterConfig(octoprint_url="http://octopi.local", octoprint_api_key="key")
    provider = OctoPrintProvider(config)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_OCTOPRINT_ERROR
    mock_get.return_value = mock_resp

    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)
    res = provider.get_status(now)

    assert res["state"] == "error"
    assert res["job_name"] is None


@patch("httpx.get")
def test_octoprint_provider_offline_failures(mock_get) -> None:
    config = PrinterConfig(octoprint_url="http://octopi.local", octoprint_api_key="key")
    provider = OctoPrintProvider(config)

    # 1. Connection failed
    mock_get.side_effect = Exception("Connection refused")
    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)
    res = provider.get_status(now)
    assert res["state"] == "offline"

    # 2. HTTP Error status 503
    mock_get.side_effect = None
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_get.return_value = mock_resp
    res = provider.get_status(now)
    assert res["state"] == "offline"


def test_no_post_routes_exist_in_provider() -> None:
    # Grep-like inspection to ensure no write/post methods exist in the adapter
    from printer.provider import OctoPrintProvider

    methods = [m[0] for m in inspect.getmembers(OctoPrintProvider, predicate=inspect.isfunction)]
    # The only methods should be init and get_status
    assert set(methods).issubset({"__init__", "get_status"})


@patch("httpx.post")
def test_run_printer_status_ingests_event(mock_post, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_PRINTER", "True")
    monkeypatch.setenv("CONTROL_PLANE_URL", "http://127.0.0.1:8090")
    monkeypatch.setenv("CONTROL_PLANE_ADMIN_TOKEN", "test-admin")

    settings = Settings()
    provider = MockPrinterProvider("printing")

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {"token": "tok_ingest_123"}

    ingest_response = MagicMock()
    ingest_response.status_code = 200
    ingest_response.json.return_value = {"status": "stored"}

    mock_post.side_effect = [token_response, ingest_response]

    res = run_printer_status(settings, provider)
    assert res is not None

    ingest_call = mock_post.mock_calls[1]
    body = ingest_call.kwargs["json"]

    batch_events = body["events"]
    assert len(batch_events) == 1
    event = batch_events[0]
    assert event["type"] == "ambient.printer_status"

    payload = event["payload"]
    assert payload["state"] == "printing"
    assert payload["job_name"] == "benchy_0.2mm_pla.gcode"
    assert payload["progress_pct"] == 64.5
    assert payload["eta"] is not None


@patch("httpx.post")
def test_run_printer_status_degrades_to_offline_on_provider_error(
    mock_post, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FEATURE_PRINTER", "True")
    monkeypatch.setenv("CONTROL_PLANE_URL", "http://127.0.0.1:8090")
    monkeypatch.setenv("CONTROL_PLANE_ADMIN_TOKEN", "test-admin")

    settings = Settings()

    # Provider that fails to reach the printer: the job must still post an
    # "offline" status event rather than crash or go silent.
    provider = MagicMock()
    provider.get_status.side_effect = Exception("connection refused")

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {"token": "tok_ingest_123"}

    ingest_response = MagicMock()
    ingest_response.status_code = 200
    ingest_response.json.return_value = {"status": "stored"}

    mock_post.side_effect = [token_response, ingest_response]

    res = run_printer_status(settings, provider)
    assert res is not None

    ingest_call = mock_post.mock_calls[1]
    payload = ingest_call.kwargs["json"]["events"][0]["payload"]
    assert payload["state"] == "offline"
    assert payload["job_name"] is None
    assert payload["progress_pct"] is None
    assert payload["eta"] is None
