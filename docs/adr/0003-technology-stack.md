# ADR-0003: Technology stack

**Status:** Accepted · **Date:** 2026-07-04

## Context

The handoff recommends Python/FastAPI backends, TypeScript/React frontends, SQLite/Postgres, MQTT/UART/WebSockets, and MediaMTX. Tooling specifics were left open.

## Decision

- **Python 3.12+** for all backend services. FastAPI + Pydantic v2, SQLAlchemy + Alembic, asyncio where useful. Monorepo Python workspace managed with **uv**; lint/format with **Ruff**; typing enforced with **Pyright**; tests with **pytest**.
- **TypeScript (strict)** for all UIs. **React + Vite** SPA, **pnpm** workspace. Kiosk deployment via Chromium.
- **Contracts:** single source of truth in `packages/contracts` as Pydantic v2 models; JSON Schema exported; TypeScript types generated from the schemas (generation tool chosen in T-002, e.g. `json-schema-to-typescript`). Hand-written duplicate types are forbidden.
- **Data:** SQLite in WAL mode on the door Pi (events, queues, enrollment); PostgreSQL on the NUC; NAS holds archives only.
- **Messaging:** UART (preferred) / UDP Pi↔ESP32; WebSockets Pi-local; Mosquitto MQTT on the NUC for control-plane fan-out and audit.
- **Media:** MediaMTX; libcamera/`rpicam-apps` capture; H.264; WebRTC for local live view (HLS is not the primary door protocol).
- **Firmware:** ESP32-S3 with ESP-IDF (FreeRTOS), C/C++. Arduino-core allowed only if an ADR supersedes this. Rationale: watchdog, task priorities, and UART control are first-class in IDF.
- **Observability:** OpenTelemetry-compatible structured JSON logging via `packages/observability`; Prometheus-style `/metrics` per service.

## Consequences

Agents do not introduce alternative frameworks, package managers, or serialization formats. New dependencies beyond a brief's list require escalation. The stack is boring on purpose — novelty budget is spent on the product, not the toolchain.
