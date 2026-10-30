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
COPY configs/empirical-rules.yaml ./configs/empirical-rules.yaml
COPY configs/reconstruction-v10-legacy-context.json ./configs/reconstruction-v10-legacy-context.json
COPY configs/feature-schema-v1.json ./configs/feature-schema-v1.json
COPY configs/item-rules ./configs/item-rules
COPY configs/ranking ./configs/ranking
# Revisão da partida (F1, CC-1003/CC-1004): referência agregada e modelo de chance de vitória.
COPY configs/match-review ./configs/match-review

# ---- test: inclui dependencias de dev (pytest/ruff/mypy) e testes ----
FROM base AS test
# Mesmas dependências da CI (dev + ml: catboost/numpy usados pelos testes de ranking e suporte).
RUN uv sync --frozen --group ml
COPY backend/tests ./tests
# Exemplo público da lista de sementes (CC-206): um teste garante que ele acompanha o carregador.
COPY configs/collection-seeds.example.yaml ./configs/collection-seeds.example.yaml
# Registro do gate do interpretador (CC-506): um teste confere que o padrão segue o gate.
COPY configs/objective-parser-gate.yaml ./configs/objective-parser-gate.yaml
# Políticas públicas do dataset (CC-401), validadas pelos testes do corpus.
COPY configs/dataset.yaml ./configs/dataset.yaml
COPY configs/experiments/dataset-16.19-v2.yaml ./configs/experiments/dataset-16.19-v2.yaml
COPY backend/openapi.json ./openapi.json
ENV PATH="/app/.venv/bin:$PATH"
CMD ["pytest"]

# ---- runtime: api e worker compartilham esta imagem, comando definido no compose ----
FROM base AS runtime
RUN uv sync --frozen --no-dev
# Código e dependências permanecem do root e legíveis ao usuário de execução.
# Somente os dados abaixo precisam de escrita; evitar copiar toda a venv no overlay.
RUN useradd --create-home --uid 1000 riftlab
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
