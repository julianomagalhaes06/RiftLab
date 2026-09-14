# syntax=docker/dockerfile:1
FROM python:3.12.3-slim-bookworm AS base

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock backend/README.md backend/alembic.ini ./
COPY backend/src ./src
COPY backend/migrations ./migrations

# ---- test: inclui dependencias de dev (pytest/ruff/mypy) e testes ----
FROM base AS test
RUN uv sync --frozen
COPY backend/tests ./tests
ENV PATH="/app/.venv/bin:$PATH"
CMD ["pytest"]

# ---- runtime: api e worker compartilham esta imagem, comando definido no compose ----
FROM base AS runtime
RUN uv sync --frozen --no-dev
RUN useradd --create-home --uid 1000 riftlab && chown -R riftlab:riftlab /app
USER riftlab
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
ENTRYPOINT ["cc"]
CMD ["--help"]
