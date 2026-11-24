# Use a Python image with uv pre-installed
FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim@sha256:da0f1f04404173d7d2fa158112e09624d5a80262efa86c89882b68389dfd44eb

# Pick up Debian security fixes published since the base image was built
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

# Setup a non-root user
RUN groupadd --system --gid 999 nonroot \
 && useradd --system --gid 999 --uid 999 --create-home nonroot

# Install the project into `/app`
WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

# Ensure installed tools can be executed out of the box
ENV UV_TOOL_BIN_DIR=/usr/local/bin

# Install the project's dependencies using the lockfile and settings
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Then, add the rest of the project source code and install it
# Installing separately from its dependencies allows optimal layer caching
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Use the non-root user to run our application
USER nonroot


ENTRYPOINT ["kopf", "run", "/app/main.py"]
