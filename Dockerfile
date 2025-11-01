# syntax=docker/dockerfile:1.7

# Base image can be overridden at build time if needed
ARG PYTHON_IMAGE=python:3.13-slim

########################
# Builder (install deps)
########################
FROM ${PYTHON_IMAGE} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install minimal native tooling for installing Python deps and uv itself
RUN --mount=type=cache,target=/var/cache/apt \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      curl ca-certificates build-essential && \
    rm -rf /var/lib/apt/lists/*

# Install uv (single-file binary) and make it available on PATH
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    echo 'export PATH="${HOME}/.local/bin:${PATH}"' >> /etc/profile
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# Copy only files required to resolve/install dependencies first
COPY pyproject.toml ./
COPY uv.lock ./

# Copy project source
COPY src ./src

# Sync dependencies and install the project into a local virtualenv (.venv)
#   - Use BuildKit cache to speed up rebuilds
#   - Use the lock file for reproducible installs
RUN --mount=type=cache,target=/root/.cache/uv \
    uv --version && \
    uv sync --frozen

########################
# Runtime (slim, non-root)
########################
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Create an unprivileged user
RUN useradd -r -u 10001 -g root appuser

# Copy the virtualenv and minimal runtime files
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/pyproject.toml /app/pyproject.toml

# Use the venv by default
ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 7957

# Mount point for VRChat logs from the host
VOLUME ["/vrchat-logs"]

USER 10001

# Console script defined in pyproject: [project.scripts]
# Keep ENTRYPOINT lean; provide safe defaults via CMD (override with docker run args)
ENTRYPOINT ["vrc-log-video-sync"]
CMD ["--host", "0.0.0.0", "--port", "7957", "--log-dir", "/vrchat-logs"]

# Basic liveness check for the HTTP endpoint
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; u=urllib.request.urlopen('http://127.0.0.1:7957/state', timeout=2); sys.exit(0 if u.status==200 else 1)"]
