# riftlab (backend)

Monólito FastAPI do RiftLab. Consulte o README público na raiz do repositório para o
contexto do produto. Sprint 1 cobriu a fundação técnica (CC-101: configuração tipada,
health checks, worker mínimo, entrypoint `cc`), a persistência inicial (CC-103:
migrations Alembic, fronteiras de domínio/repositórios para catálogo, partidas,
participantes, eventos, jobs de importação e estados de decisão) e o gate de contratos
(CC-106: erro padronizado em `application/problem+json`, OpenAPI real das rotas de saúde,
schemas Pydantic dos contratos de sprints futuras em `src/riftlab/shared/api/`). Sprint 2
entregou o importador real (CC-201, abaixo). Reconstrução temporal, ranking e análises
Reconstrução pré-compra de inventário chegou no CC-203 (abaixo). Ranking, objetivos e
modo Estudo chegam em cards posteriores.

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

## Reconstrução de inventário (CC-203)

Exige a partida já importada (seção Importação acima, `POST /api/v1/imports` ou
`process_next_job`) e o catálogo do patch da partida já sincronizado (seção Catálogo).

```sh
uv run cc timeline reconstruct --mode demo --match-id DEMO-0001   # fixture sintética, sem rede
uv run cc timeline reconstruct --mode real --match-id <matchId>   # raw já importado (CC-201), sem coleta nova
```

`src/riftlab/timeline/application.py:reconstruct_match` resolve o catálogo da partida
uma única vez (fixado, nunca reconsultado durante o replay), carrega os eventos já
persistidos (`timeline_events`, CC-201) e relê a mesma fonte local só para os frames
(nunca persistidos pelo CC-201, nunca uma coleta nova — raw imutável, releitura local).
`reconstruct_participant_purchases` (puro, sem I/O) reduz os eventos de cada
participante em ordem `(timestamp_ms, source_index)`, guardando o inventário
imediatamente ANTES de cada compra (nunca o item comprado aparece no próprio estado):
multiconjunto de itens com trinket em slot separada (achado real: todo item com a tag
`"Trinket"` no catálogo real tem receita vazia), receita consumida por quantidade
possuída inclusive por subcomponentes transitivos nunca comprados diretamente (compra
direta paga o restante, nunca exige posse total), 6 slots normais verificados contra as
regras de empilhamento do catálogo (`flags.consumed`/`flags.stacks`), venda, undo
(restaura o multiconjunto exato de uma correlação verificada por invariante interno —
`beforeId` E `afterId` conferidos contra a operação reversível mais recente, nunca um
palpite) e ouro estimado a partir do último frame estritamente anterior ao evento (nunca
interpolado, nunca negativo). Ambiguidade (item desconhecido do catálogo, destruição não
reconciliada, capacidade excedida, regra de empilhamento desconhecida, undo sem
correlação única, catálogo ausente para o patch) fica marcada como incerta de forma
explícita e datada, nunca escondida nem "reparada" pelo inventário final. Persistência
idempotente por uma marca atômica própria e versionada (`match_reconstructions`,
`RECONSTRUCTION_ALGORITHM_VERSION`): reconstruir a mesma partida de novo devolve os
mesmos estados, nunca duplica, independente de qual participante teve compra; uma
versão anterior do algoritmo nunca é apagada nem confundida com a atual.

**Evidência empírica e limites**: a reconciliação entre uma compra de receita e os
`ITEM_DESTROYED` que ela dispara, e a correlação de `ITEM_UNDO`, usam regras internas
sobre semântica que a Riot não documenta. Foram examinadas offline num corpus real
pequeno — 10 partidas dos patches 14.20 e 15.1, com os catálogos históricos
correspondentes do Data Dragon público — e o resultado orientou o reducer: nesse corpus,
todos os 851 `ITEM_DESTROYED` que têm compra no mesmo timestamp precedem a compra (os
componentes são destruídos antes de o item composto aparecer), e 759 dos 783 destroys
compatíveis por ID com a receita de uma compra co-temporal foram alocados à compra
correspondente; um undo de venda usa `beforeId=0`/`afterId=<item vendido>`, forma oposta
à do undo de compra. Isso é consistência com uma amostra pequena e de dois patches, não
semântica oficial: os patches do recorte congelado (16.16/16.17) não foram avaliados, os
dados brutos não são versionados, e o que não pôde ser reconciliado continua marcado
como incerto. Bater com uma regra interna não comprova a semântica real: fora do modo
demo (dado sintético, verificado pela suíte de testes), aplicar qualquer uma das duas
regras sempre marca `quality.uncertain=true` a partir dali — nunca apenas documentado,
sempre um gate operacional (`UNVALIDATED_DESTROY_RECONCILIATION`/
`UNVALIDATED_DESTROY_ORDER_RECONCILIATION`/`UNVALIDATED_UNDO_SEMANTICS` em
`quality.reasons`).

**Consumo pré-decisão** (para quem usa os `DecisionState`s, como a geração de candidatos
e features): `quality.uncertain`/`quality.reasons` são cumulativos e incluem diagnósticos
que só existem por causa do item escolhido (item desconhecido, capacidade excedida,
reconciliações); não devem alimentar elegibilidade, features ou máscaras de ausência
anteriores à decisão. Para isso existe `quality.preDecisionQuality`, calculada antes de
processar a compra e independente do alvo — inclui catálogo ausente desde a primeira
compra. Como as versões anteriores do algoritmo são preservadas (nunca apagadas),
`list_decision_states` devolve estados de todas as versões: o consumidor deve filtrar por
`state_version == RECONSTRUCTION_ALGORITHM_VERSION`.

## Migrações

```sh
uv run alembic upgrade head        # aplica migrations pendentes (idempotente)
uv run cc db upgrade                # equivalente via entrypoint cc
uv run cc db current                # mostra a revisão atual do banco configurado
```

Requer `RIFTLAB_DATABASE_URL` apontando para um PostgreSQL real (SQLite não é
suportado). Em produção/dev via Compose: `docker compose run --rm api db upgrade`
(o entrypoint da imagem já é `cc` — não repetir o nome do comando).
