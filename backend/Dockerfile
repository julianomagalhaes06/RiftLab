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
# Settings.catalog_mappings_path (CC-202) aponta para este caminho absoluto
# em ambas as imagens (test e runtime) — mesmo racional de raw_storage_dir.
COPY configs/catalog-mappings.yaml ./configs/catalog-mappings.yaml
# Recorte real (scope.yaml, CC-105) e schema de features (CC-204): lidos pelo módulo de
# decisões e pelos testes que comparam o schema com a allowlist do código.
COPY configs/scope.yaml ./configs/scope.yaml
COPY configs/feature-schema-v1.json ./configs/feature-schema-v1.json

# ---- test: inclui dependencias de dev (pytest/ruff/mypy) e testes ----
FROM base AS test
RUN uv sync --frozen
COPY backend/tests ./tests
COPY backend/openapi.json ./openapi.json
ENV PATH="/app/.venv/bin:$PATH"
CMD ["pytest"]

# ---- runtime: api e worker compartilham esta imagem, comando definido no compose ----
FROM base AS runtime
RUN uv sync --frozen --no-dev
RUN useradd --create-home --uid 1000 riftlab && chown -R riftlab:riftlab /app
# Dono correto antes do volume nomeado do worker (CC-201, compose.yaml:
# riftlab_raw_data em /app/data/raw) ser montado por cima — Docker copia
# conteudo/permissoes de um caminho ja existente na imagem para dentro do
# volume na primeira montagem; sem isso o ponto de montagem nasce root:root e
# o processo riftlab (nao-root) nunca consegue escrever ali.
RUN mkdir -p /app/data/raw && chown -R riftlab:riftlab /app/data
USER riftlab
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
ENTRYPOINT ["cc"]
CMD ["--help"]
