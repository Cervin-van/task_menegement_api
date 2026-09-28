# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app

# Prod deps only; cached layer until pyproject/uv.lock change.
FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# Tests: prod + dev deps.
FROM deps AS test
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
COPY . .
ENV PATH="/opt/venv/bin:$PATH"
CMD ["pytest", "-q"]

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=deps /opt/venv /opt/venv
COPY --chown=app:app . .
RUN chmod +x scripts/*.sh
USER app
EXPOSE 8000
ENTRYPOINT ["scripts/entrypoint.sh"]
CMD ["api"]
