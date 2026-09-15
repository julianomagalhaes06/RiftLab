# riftlab (backend)

Monólito FastAPI do RiftLab. Consulte o README público na raiz do repositório para o
contexto do produto. Esta etapa cobre a fundação técnica (CC-101: configuração tipada,
health checks, worker mínimo, entrypoint `cc`), a persistência inicial (CC-103:
migrations Alembic, fronteiras de domínio/repositórios para catálogo, partidas,
participantes, eventos, jobs de importação e estados de decisão) e o gate de contratos
(CC-106: erro padronizado em `application/problem+json`, OpenAPI real das rotas de saúde,
schemas Pydantic dos contratos de sprints futuras em `src/riftlab/shared/api/`, sem
nenhuma rota nova montada). Importação real, reconstrução temporal, ranking e análises
chegam em sprints posteriores.

## Contratos e OpenAPI (CC-106)

```sh
uv run cc openapi export openapi.json   # exporta o schema real (so /health/live e /health/ready)
```

`openapi.json` é versionado e usado pelo frontend para gerar tipos TypeScript
(`frontend/npm run generate:api-types`); `tests/contract/test_openapi_contract.py`
falha se o arquivo divergir da aplicação real. Erros seguem `application/problem+json`
(`src/riftlab/errors.py`): 404 (rota/recurso inexistente), 422 (validação, nunca ecoa o
valor bruto submetido) e 500 (falha não tratada) além do 503 de `/health/ready`. Os
contratos de rotas futuras (importação, partidas, análises, aprendizagem) existem como
Pydantic em `src/riftlab/shared/api/`, validados por `tests/contract/test_shared_api_contracts.py`,
mas nenhum deles está montado como endpoint — ver `docs/contracts.md` para a matriz
completa.

## Migrações

```sh
uv run alembic upgrade head        # aplica migrations pendentes (idempotente)
uv run cc db upgrade                # equivalente via entrypoint cc
uv run cc db current                # mostra a revisão atual do banco configurado
```

Requer `RIFTLAB_DATABASE_URL` apontando para um PostgreSQL real (SQLite não é
suportado). Em produção/dev via Compose: `docker compose run --rm api cc db upgrade`.
