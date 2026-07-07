# control-plane-api (T-501) packaged for the NUC compose stack (T-503).
#
# Build context is the repo root (this is a uv workspace; the service
# depends on sibling packages/contracts, packages/auth, packages/config via
# workspace path deps) — see infra/compose/docker-compose.yml /
# compose.dev.yml `build.context`.
#
# Packaging only: no application logic lives in this file. control-plane-api
# itself is out of scope for T-503 (agent:codex, T-501) — this Dockerfile
# just gives it a container to run in.

FROM ghcr.io/astral-sh/uv:0.5-python3.12-bookworm-slim AS builder
WORKDIR /src

COPY pyproject.toml uv.lock ./
COPY packages/contracts packages/contracts
COPY packages/auth packages/auth
COPY packages/config packages/config
COPY packages/observability packages/observability
COPY packages/esp32-link packages/esp32-link
COPY packages/media-client packages/media-client
COPY apps/control-plane-api apps/control-plane-api
COPY apps/door-api apps/door-api
COPY apps/door-visiond apps/door-visiond
COPY apps/door-media apps/door-media
COPY apps/door-sync apps/door-sync
COPY apps/wallboard-worker apps/wallboard-worker
COPY apps/simulator apps/simulator

RUN uv sync --frozen --no-dev --package doorboard-control-plane-api

FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app

RUN groupadd --system doorboard && useradd --system --gid doorboard --create-home doorboard
COPY --from=builder /src/.venv /app/.venv
COPY --from=builder /src/apps/control-plane-api /app/apps/control-plane-api

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

USER doorboard

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=3).status == 200 else 1)"]

EXPOSE 8090
CMD ["control-plane-api", "--host", "0.0.0.0", "--port", "8090"]
