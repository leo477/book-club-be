# Stage 1: builder
FROM python:3.15-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /bin/uv

WORKDIR /app

ARG CACHEBUST=1
RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock .python-version ./

# SCRUM-185: never let uv download a managed interpreter (the venv python would be a
# symlink into /root/.local/share/uv, absent from the runtime stage). Use the image's
# /usr/local/bin/python3.x, which is identical in the runtime stage.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never UV_PYTHON_PREFERENCE=only-system

RUN uv sync --frozen --no-dev --no-install-project

# Stage 2: runtime
FROM python:3.15-slim-bookworm

ARG CACHEBUST=1
RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
    openssl \
    libssl3 \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid appuser --shell /bin/sh --create-home appuser

COPY --from=builder /app/.venv /app/.venv
COPY app/ /app/app/
COPY alembic/ /app/alembic/
COPY alembic.ini /app/alembic.ini

ENV PATH="/app/.venv/bin:$PATH"

# SCRUM-166: uvicorn reads FORWARDED_ALLOW_IPS (no CLI flag needed). Only Render's
# private proxy range may supply X-Forwarded-For; never "*" (client-spoofable).
ENV FORWARDED_ALLOW_IPS="10.0.0.0/8"

WORKDIR /app

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["/app/.venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
