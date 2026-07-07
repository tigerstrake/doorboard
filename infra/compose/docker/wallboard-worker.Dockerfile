# wallboard-worker packaging placeholder (T-503).
#
# wallboard-worker itself has no jobs yet — those land in T-601..T-605
# (agent:gemini, M6, both depend on T-503). Writing that job logic now would
# be a service-code change outside this brief's fence, so this image does
# nothing beyond proving the workspace installs and the package imports.
# T-601 replaces the CMD below with a real entrypoint; no other change to
# this Dockerfile should be needed then.
#
# The compose service using this image is behind the `future` profile (see
# infra/compose/README.md) — it does not start with a plain `docker compose
# up` and is not part of the "all healthchecks green" acceptance bar.

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

RUN uv sync --frozen --no-dev --package doorboard-wallboard-worker

FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app

RUN groupadd --system doorboard && useradd --system --gid doorboard --create-home doorboard
COPY --from=builder /src/.venv /app/.venv
COPY --from=builder /src/apps/wallboard-worker /app/apps/wallboard-worker

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

USER doorboard

CMD ["python", "-c", "import wallboard_worker; print('wallboard-worker placeholder: no jobs until T-601..T-605'); import time; time.sleep(2**31)"]
