"""CORS origin configuration for door-api.

The on-Pi kiosk build only ever needs the two localhost dev origins. To let the
owner open /admin from a laptop on the LAN, DOOR_API_CORS_ORIGINS can add extra
origins (e.g. http://door-pi.local:5173) without changing the default policy.
"""

from __future__ import annotations

import os

import pytest
from door_api.config import SessionConfig
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

# Ensure the shared app module can be imported regardless of test ordering.
os.environ.setdefault("DOOR_API_DB_PATH", ":memory:")
os.environ.setdefault("DOOR_API_SOCIAL_DB_PATH", ":memory:")

DEFAULT_ORIGINS = ("http://127.0.0.1:5173", "http://localhost:5173")


def test_cors_origins_default_to_localhost_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.delenv("DOOR_API_CORS_ORIGINS", raising=False)

    assert SessionConfig.from_env().cors_origins == DEFAULT_ORIGINS


def test_cors_origins_append_configured_extras_trimmed_and_deduped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv(
        "DOOR_API_CORS_ORIGINS",
        # extra whitespace, a blank entry, and a duplicate of a default
        " http://door-pi.local:5173 , ,http://10.0.0.5:5173 , http://localhost:5173 ",
    )

    assert SessionConfig.from_env().cors_origins == (
        *DEFAULT_ORIGINS,
        "http://door-pi.local:5173",
        "http://10.0.0.5:5173",
    )


def test_cors_preflight_allows_default_origin() -> None:
    from door_api.app import app  # imported with DOOR_API_CORS_ORIGINS unset

    client = TestClient(app)
    resp = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_preflight_allows_configured_extra_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_CORS_ORIGINS", "http://door-pi.local:5173")

    # door_api.app is a module-level singleton whose CORS middleware bakes its
    # origins at import time; reloading it would corrupt state other tests share.
    # Wire an isolated app the same way app.py does, from the resolved config, so
    # a configured extra origin is exercised through a real CORS preflight.
    isolated = FastAPI()
    isolated.add_middleware(
        CORSMiddleware,
        allow_origins=list(SessionConfig.from_env().cors_origins),
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @isolated.get("/health")
    def _health() -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(isolated)
    resp = client.options(
        "/health",
        headers={
            "Origin": "http://door-pi.local:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://door-pi.local:5173"
