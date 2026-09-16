# riftlab (backend)

Monólito FastAPI do RiftLab. Consulte o README público na raiz do repositório para o
contexto do produto. Sprint 1 cobriu a fundação técnica (CC-101: configuração tipada,
health checks, worker mínimo, entrypoint `cc`), a persistência inicial (CC-103:
migrations Alembic, fronteiras de domínio/repositórios para catálogo, partidas,
participantes, eventos, jobs de importação e estados de decisão) e o gate de contratos
(CC-106: erro padronizado em `application/problem+json`, OpenAPI real das rotas de saúde,
schemas Pydantic dos contratos de sprints futuras em `src/riftlab/shared/api/`). Sprint 2
entregou o importador real (CC-201, abaixo). Reconstrução temporal, ranking e análises
chegam em cards posteriores.

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

## Importação (CC-201)

```sh
curl -X POST http://127.0.0.1:8000/api/v1/imports \
  -H "Content-Type: application/json" -d '{"matchId":"DEMO-0001","mode":"DEMO"}'
curl http://127.0.0.1:8000/api/v1/imports/<jobId>   # polling
```

`POST /api/v1/imports`, `GET .../{id}` e `POST .../{id}/retry` são rotas reais
(`src/riftlab/api/routes/imports.py`). Job durável em `import_jobs`: o worker reserva
com `SELECT ... FOR UPDATE SKIP LOCKED` (lease 120s, token de posse
`lease_generation` — uma escrita final com generation desatualizada nunca é aplicada),
busca a partida via `DemoMatchSource` (mode=DEMO, fixtures empacotadas) ou
`RiotMatchSource` (mode=RIOT, Match-V5/Timeline-V5 real — só aceita
`endOfGameResult=GameComplete`), valida e persiste match/participantes/eventos numa
única transação atômica antes de marcar `SUCCEEDED`. Até 5 tentativas transitórias
(timeout/5xx/429, backoff exponencial com jitter, nunca ignora `Retry-After`) antes de
`FAILED` permanente; 401/403/404/schema incompatível falham direto, sem retry.

Modo RIOT exige `RIFTLAB_RIOT_API_KEY` **e** `RIFTLAB_SUBJECT_HASH_SECRET`
(HMAC-SHA256 do PUUID — nunca persistido bruto) configuradas no servidor; ausência de
qualquer uma delas recusa o job antes de qualquer rede/persistência. Corpos brutos
match+timeline ficam num armazenamento local durável
(`src/riftlab/ingestion/infrastructure/raw_store.py`, volume `riftlab_raw_data` no
Compose), reaproveitado sem nova rede se o worker for reiniciado.

## Catálogo (CC-202)

```sh
uv run cc catalog sync --mode demo --patch DEMO-1              # fixture sintética, sem rede
uv run cc catalog sync --mode real --patch 16.17                # Data Dragon público real
```

`configs/catalog-mappings.yaml` mapeia `game_patch` real (major.minor, mesmo
formato de `matches.patch`) para a versão do Data Dragon — um patch sem
entrada aí recusa o sync antes de qualquer rede (`MISSING_CATALOG`), nunca
cai para "latest". `DataDragonCatalogSource`
(`src/riftlab/catalog/infrastructure/data_dragon_source.py`) busca
`item.json` real do CDN público (`ddragon.leagueoflegends.com`, sem
credencial), curadoria (`curation.py`) separa normalização de campos
documentados (preço, receita como multiconjunto explícito, nunca um
conjunto) de itens sem reconciliação numérica (`specialRecipe`, sem `name`,
ou dependentes de um item excluído) — excluídos com motivo explícito, nunca
forçados a caber no formato nem derrubando o catálogo inteiro. Sincronização
idempotente e imutável: `catalog_versions.sha256` é a identidade de
conteúdo (raw + mapping + curadoria); mesmo conteúdo nunca duplica linha,
conteúdo divergente nunca sobrescreve uma versão já publicada.

## Migrações

```sh
uv run alembic upgrade head        # aplica migrations pendentes (idempotente)
uv run cc db upgrade                # equivalente via entrypoint cc
uv run cc db current                # mostra a revisão atual do banco configurado
```

Requer `RIFTLAB_DATABASE_URL` apontando para um PostgreSQL real (SQLite não é
suportado). Em produção/dev via Compose: `docker compose run --rm api db upgrade`
(o entrypoint da imagem já é `cc` — não repetir o nome do comando).
