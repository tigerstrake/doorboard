# wallboard-worker runtime for the NUC control plane.

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
COPY integrations integrations

RUN uv sync --frozen --no-dev --no-editable --package doorboard-wallboard-worker

FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app

RUN groupadd --system doorboard && useradd --system --gid doorboard --create-home doorboard
COPY --from=builder /src/.venv /app/.venv
COPY --from=builder /src/apps/wallboard-worker /app/apps/wallboard-worker

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

USER doorboard

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import os,sys,time; p='/tmp/wallboard-worker-heartbeat'; sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p)<120 else 1)"]

CMD ["wallboard-worker", "run"]
