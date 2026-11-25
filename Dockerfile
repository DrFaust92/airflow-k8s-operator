# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Build stage: resolve the locked environment into /app/.venv.
#
# The runtime image is distroless and ships Debian's python3 (3.13 on
# trixie), so the venv is built against the same Debian python3 here. Both
# stages must stay on the same Debian release.
# ---------------------------------------------------------------------------
FROM debian:trixie-slim@sha256:8aa5a7bf5a1955bddbe7d74fcdd0b91ab30e74362a8b73af672b0481b35b44a0 AS build

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f /uv /usr/local/bin/uv

WORKDIR /app

# Use the system (Debian) interpreter, never a uv-managed download, and
# precompile bytecode so the read-only runtime filesystem needs no writes.
ENV UV_PYTHON=/usr/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Dependencies first for layer caching, then the project source.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ---------------------------------------------------------------------------
# Runtime stage: distroless (no shell, no package manager, no pip).
# ---------------------------------------------------------------------------
FROM gcr.io/distroless/python3-debian13:nonroot@sha256:e36c26ad81ef8c9da7605d717a22160193ab66cfbc55bd5977f429f01f6ead99

WORKDIR /app
COPY --from=build /app /app

ENV PATH="/app/.venv/bin:/usr/local/bin:/usr/bin:/bin" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Numeric uid/gid matching the chart's securityContext (runAsUser/runAsGroup 999).
USER 999:999

ENTRYPOINT ["/app/.venv/bin/kopf", "run", "/app/main.py"]
