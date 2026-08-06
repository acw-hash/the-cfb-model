# Maintained but unused for Tasks 1–24: native uv is the default runtime.
# NVIDIA CUDA 12.x runtime on Ubuntu 22.04 — reserved for a later GPU/cloud phase.
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/home/app/.local/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /usr/local/bin/uv

RUN useradd --create-home --shell /bin/bash app
WORKDIR /app

COPY --chown=app:app pyproject.toml uv.lock ./
COPY --chown=app:app src ./src
COPY --chown=app:app configs ./configs
COPY --chown=app:app docs ./docs

# /app is created as root; grant ownership so non-root `uv sync` can write .venv
RUN chown app:app /app

USER app

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

CMD ["ncaa-quant", "--help"]
