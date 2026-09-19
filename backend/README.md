# riftlab (backend)

Monólito FastAPI do RiftLab. Consulte o README público na raiz do repositório para o
contexto do produto. Sprint 1 cobriu a fundação técnica (CC-101: configuração tipada,
health checks, worker mínimo, entrypoint `cc`), a persistência inicial (CC-103:
migrations Alembic, fronteiras de domínio/repositórios para catálogo, partidas,
participantes, eventos, jobs de importação e estados de decisão) e o gate de contratos
(CC-106: erro padronizado em `application/problem+json`, OpenAPI real das rotas de saúde,
schemas Pydantic dos contratos de sprints futuras em `src/riftlab/shared/api/`). Sprint 2
entregou o importador real (CC-201), o catálogo versionado (CC-202), a reconstrução
pré-compra de inventário (CC-203) e os pontos de decisão com features versionadas e as
rotas de leitura de decisões (CC-204), todos abaixo. Ranking, objetivos e modo Estudo
chegam em cards posteriores.

## Contratos e OpenAPI (CC-106)

```sh
uv run cc openapi export openapi.json   # exporta o schema real das rotas montadas
```

`openapi.json` é versionado e usado pelo frontend para gerar tipos TypeScript
(`frontend/npm run generate:api-types`); `tests/contract/test_openapi_contract.py`
falha se o arquivo divergir da aplicação real. Erros seguem `application/problem+json`
(`src/riftlab/errors.py`): 404 (rota/recurso inexistente), 409 (conflito; para decisões,
o código `DECISIONS_NOT_PREPARED`), 422 (validação, nunca ecoa o valor bruto submetido;
para cursor inválido, o código `INVALID_CURSOR`) e 500 (falha não tratada) além do 503 de
`/health/ready`. Estão montadas as rotas de saúde, de importação e as duas de decisões
(abaixo). Os contratos das rotas futuras (lista/detalhe de partidas, análises,
aprendizagem) existem como Pydantic em `src/riftlab/shared/api/`, validados por
`tests/contract/test_shared_api_contracts.py`, mas nenhum deles está montado como
endpoint.

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

**Transação destroy→compra (versão 6)**: na forma real observada, os `ITEM_DESTROYED` dos
componentes vêm ANTES da compra composta, no mesmo instante. Os destroys que a compra
reivindica fazem parte da transação dela: o ponto de decisão fica antes deles
(`inventory_before` traz as unidades reivindicadas e a qualidade pré-decisão não carrega
as marcas desses destroys), as marcas `UNRECONCILED_ITEM_DESTROYED` deles saem do
cumulativo (a remoção vale só dentro do mesmo instante; estados já calculados nunca são
alterados) e o undo da compra restaura os componentes. Um destroy que nenhuma compra
reivindica continua conservador (removido e marcado). Em dado real, `UNVALIDATED_DESTROY_ORDER_RECONCILIATION`
segue marcando as decisões seguintes. Nas versões 3 a 5 o snapshot omitia exatamente a
receita do próprio alvo e toda compra composta de forma real ficava incerta.

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

## Pontos de decisão e rotas de leitura (CC-204)

Fluxo local completo (tudo em banco; nenhum passo depois da importação usa rede):

```sh
uv run cc catalog sync --mode demo --patch DEMO-1          # 1. catálogo
# 2. importação (POST /api/v1/imports ou process_next_job)
uv run cc timeline reconstruct --mode demo --match-id DEMO-0008   # 3. reconstrução v6
uv run cc decisions extract --match-id DEMO-0008            # 4. extração (só banco)
```

`cc decisions extract` imprime um resumo sem dado pessoal (versões, total de pontos,
elegíveis, contagem por motivo e se a extração foi reaproveitada). É idempotente por
partida e tupla de versões; exige a reconstrução na versão corrente (senão
`RECONSTRUCTION_REQUIRED`) e nunca reconstrói, abre fonte nem acessa rede.

- **Catálogo e classificação**: a curadoria `data_dragon_v5` persiste `attributes.into`
  (lista ordenada de `int`, validada como `from`; só dígitos ASCII). O classificador puro
  `catalog/classification.py` (`item_classification_v1`) aplica regras fixas por
  precedência (não comprável, mapa 11 ilegal, exclusivo de campeão, trinket, consumível,
  botas por tag ou por receita transitiva, componente por `into`, básico, principal). Nunca
  usa nome, descrição nem preço; campo ausente, receita com componente desconhecido ou
  curadoria sem suporte resulta em `UNCLASSIFIED`. Limitação conhecida: itens de id ≥ 100000
  que repetem o nome de um item real (ex.: `323003`) passam como principais.
- **Seleção e elegibilidade** (`decisions/`, módulo puro `decision_selection_v1`): o alvo
  principal gera um ponto de decisão (alvo conhecido e não principal não gera; alvo
  desconhecido, excluído, sem classificação, sem catálogo, desfeito por undo ou de status
  incerto gera um ponto listado como inelegível). Elegibilidade de contexto usa só
  `preDecisionQuality`, catálogo, recorte e a posição do item principal (0 → 1º, 1 → 2º,
  2 ou mais → `OUT_OF_SCOPE`). O recorte vem da origem: dado real usa `configs/scope.yaml`
  (`RIFTLAB_SCOPE_PATH`), o demo usa um recorte sintético explícito. O orçamento é separado
  (`STALE_FRAME`, `UNKNOWN_BUDGET`) e nunca torna a decisão inelegível.
- **Features** (`configs/feature-schema-v1.json`, `feature_schema_v1`): 18 campos com tipo,
  nulidade, proveniência, limite de observabilidade e máscara de ausência; só existem para
  pontos elegíveis e são calculadas de uma visão que não tem o item comprado, o bloco
  retrospectivo, a qualidade cumulativa nem identificador pessoal. A allowlist do código e o
  arquivo são testados um contra o outro.
- **Persistência** (migração `b8d4f1a6c3e9`): `decision_extractions` (marca por partida e
  tupla de versões, com contagem de pontos) e `decision_points`, imutáveis (sem update no
  código e gatilho no banco), com chave única `NULLS NOT DISTINCT` (a tupla tem colunas
  anuláveis) e chave estrangeira composta para o estado de origem. `decision_states` não é
  reescrita.
- **Rotas** (só leitura, transação `READ ONLY`, nunca reconstroem, extraem, abrem fonte nem
  usam rede): `GET /api/v1/matches/{matchId}/decisions?participantId=&cursor=&limit=`
  (`participantId` 1..10 obrigatório; `limit` padrão 20, máximo 100; ordem estável por
  `(timestampMs, eventSourceIndex, id)`; `nextCursor` nulo no fim) e
  `GET /api/v1/decisions/{decisionId}`. Sem extração corrente: `409 DECISIONS_NOT_PREPARED`
  (distinto de `200` com `items: []`, "extraída com zero pontos"). Cursor opaco fixado na
  extração, partida e participante; cursor inválido ou de outro escopo: `422 INVALID_CURSOR`.
  Partida, participante ou decisão inexistente: `404`. **Só informação pré-decisão** (modo
  Estudo): inventário como multiconjunto `[{itemId, quantity}]` mais o trinket, orçamento
  (valor nulo quando desconhecido), nível observado, motivos e versões. Nunca o item comprado,
  a classe dele, o resultado da compra, o vetor de features, a qualidade cumulativa nem hash
  pessoal; qualquer motivo ligado ao alvo aparece como o código neutro `TARGET_NOT_ELIGIBLE`,
  sem sub-motivos.
- **Demo**: `DEMO-0002` (compra composta direta) e `DEMO-0008` (forma real: destroys dos
  componentes antes da compra, frames próximos) geram um ponto elegível, ordinal 1, com o
  inventário anterior correto.

**Limitação real**: no corpus real local examinado (10 partidas dos patches 14.20/15.1) a
extração produziria 199 pontos de decisão e nenhum elegível: as heurísticas de dado real do
CC-203 marcam a pré-decisão como incerta de forma cumulativa, e mecânicas de inventário ainda
não modeladas (consumo de consumível, troca de trinket, transformações de item, aquisições
fora de `ITEM_PURCHASED`) mantêm as marcas. O demo valida o fluxo, não o dado real.

## Navegação de partidas e preparação automática (CC-205)

Depois de uma importação bem-sucedida o worker prepara a partida sozinho, sem console:
catálogo → reconstrução v6 → extração de decisões. A preparação é um job durável em
`import_jobs` com `kind=PREPARE` (migração `c5e9a2d7f1b8`), enfileirado na mesma transação
que conclui a importação, e reaproveita a maquinaria do importador: posse por lease com
token de geração, retomada depois de crash, backoff e teto de tentativas. No máximo uma
preparação ativa por partida (índice único parcial). É idempotente pelas marcas de
reconstrução e extração: repetir não duplica nada. Nunca coleta a partida de novo (só a
fixture demo ou o dado bruto local já importado).

- **Catálogo**: no modo demo, a fixture local, sem rede. No modo real, rede só para o
  Data Dragon público, e somente se o patch tem entrada em `configs/catalog-mappings.yaml`
  e ainda não há catálogo canônico; sem entrada não há rede e os pontos saem `MISSING_CATALOG`.
- **Estados** (`preparationStatus`): `NOT_REQUESTED`, `PENDING` (na fila ou aguardando nova
  tentativa transitória, com `errorCode` e `retryAt`), `RUNNING`, `READY` e `FAILED`
  (falha permanente com código seguro, sem payload nem stack). Erro estrutural (dado
  inválido, dado bruto ausente, mapping ausente) falha na hora; erro transitório (rede, 5xx,
  429) tenta de novo com backoff até o limite.
- **Rotas**: `GET /api/v1/matches?cursor=&limit=` (só partidas importadas; ordem estável
  `startAt` decrescente e `matchId` decrescente; cursor opaco; sem identificador pessoal),
  `GET /api/v1/matches/{matchId}` (patch, fila, mapa, participantes sem identificador pessoal e
  estado de preparação; `404` desconhecida) e `POST /api/v1/matches/{matchId}/preparation`
  (nova tentativa mínima: `202` ao enfileirar ou se já há uma ativa, `200` se já está `READY`
  com as versões correntes, `404` sem importação concluída; idempotente, histórico preservado).
  As leituras usam transação `READ ONLY` e nunca preparam nem usam rede.
- **Nome do item**: `inventory.items[].name` no detalhe da decisão vem do catálogo fixado
  pelas versões do ponto, nunca do canônico atual; `null` sem catálogo ou item sem nome.

- **Laço do worker**: a cada ciclo o worker tenta um job de importação e um de preparação, cada
  um com tratamento de exceção próprio; uma falha inesperada recorrente de um lado é logada
  (sanitizada, com a etapa) e nunca impede o outro de ser atendido.
- **Modo RIOT sem chave**: `POST /api/v1/imports` com `mode=RIOT` num servidor sem
  `RIFTLAB_RIOT_API_KEY` responde `422` com o código dedicado `RIOT_KEY_MISSING`
  (problem+json, texto fixo, nenhum job criado), distinto de `VALIDATION_ERROR`.

Fumaça ponta a ponta pela API, sem console (a stack não migra o banco sozinha):

```sh
docker compose run --rm api db upgrade
python3 scripts/smoke_import_flow.py --base-url http://127.0.0.1:8000
```

## Coleta incremental e relatórios (CC-206)

```sh
# 1) coleta (rede; chave e segredo só por .env/ambiente, nunca por argumento)
uv run cc collect run --seeds-file <sementes.yaml> --patches 16.16,16.17 --since 2026-08-10 \
    --max-matches 70 --max-matches-per-seed 35 --max-requests 250 --max-duration-seconds 1800
# 2) importar do armazenamento local (sem rede) e preparar; --no-drain só enfileira para o worker
uv run cc collect import [--no-drain]
# 3) relatórios (sem dado pessoal)
uv run cc collect report [--run N] [--json]                       # manifesto: sem rede, sem banco
uv run cc report reconstruction|diversity|integrity --only-collected [--json]
```

No Docker Compose o entrypoint já é `cc`: `docker compose run --rm worker collect run ...`.
O exemplo público da lista de sementes é `configs/collection-seeds.example.yaml`.

- **Sementes (lista permitida).** Só contas de pessoas que consentiram de forma explícita,
  com rótulo pseudônimo e `consent: true`; o arquivo só aponta para arquivos de conta
  locais (fora do Git) e recusa dado de conta inline. Nada percorre adversários, colegas
  de time, ligas ou descobertas derivadas. Ao montar a lista num contêiner, monte somente
  leitura apenas o que a coleta lê (o arquivo de sementes e as contas), não a pasta toda.
- **Filtros com contagem de perdas.** Plataforma BR1, fila 420, mapa 11, partida
  concluída, patches de `configs/scope.yaml` (`--patches` só estreita), a semente
  participa, documento válido e o PAR partida+timeline aceito pelo mesmo parser do
  importador. Cada partida coletada ou excluída (com motivo) fica no manifesto.
- **Tetos e limitação.** Requisições, partidas novas, duração e partidas por semente, por
  execução. O limitador global aprende as janelas dos headers `X-App-Rate-Limit` e
  `X-Method-Rate-Limit` (e das contagens) e respeita `Retry-After`; toda tentativa,
  inclusive retry do transporte, conta no orçamento. `--since`/`--until` viram
  `startTime`/`endTime` da listagem.
- **Paradas seguras.** 401/403 bloqueiam novas execuções até `--resume-after-auth-failure`
  (confirmação humana de que a chave foi trocada); 429 grava um cooldown que as execuções
  seguintes respeitam antes de qualquer requisição. Códigos de saída de `collect run`: `0`
  terminou ou parou num teto, `1` recusou antes de qualquer requisição, `3` credencial,
  `4` limite de taxa, `5` parcial (semente pulada ou servidor instável).
- **Retomada sem reiniciar a coleta.** Manifesto durável (JSON atômico, uma execução por
  vez, dentro de `<raw-dir>/collection/`). Uma partida só vira `FETCHED` depois de gravada
  de forma durável (par partida+timeline validado); queda entre buscar e gravar reaproveita
  o que já está em disco. A listagem da Riot cresce pela cabeça, então **cada passada
  recomeça da posição 0** (um índice guardado pularia partidas jogadas entre execuções ou
  ao mudar a janela): as partidas conhecidas custam 0 requisição e só a página de listagem
  é paga de novo (1 por 100 ids).
- **Da coleta à análise.** `collect import` enfileira a importação a partir do raw local
  (nunca busca de novo; o worker também prefere o raw local quando completo) e cada
  importação enfileira a preparação automática do CC-205. `report integrity` sai com `1`
  se alguma partida parcial for analisável (READY sem extração, partida sem
  participantes ou eventos, órfã, ou sem o par bruto).
- **Relatórios.** `collect report`: rodadas, requisições, partidas novas/duplicadas/
  excluídas por motivo, causa de parada, comportamento observado (janelas de limite,
  nomes de header, maior página) e o estado da passada por semente. `report
  reconstruction`: estados, pontos, elegíveis, motivos e sub-motivos, na visão geral e
  DENTRO DO RECORTE (mesmo código do CC-204). `report diversity`: concentração por jogador
  pseudonimizado e cobertura por patch/campeão, só agregados. `--only-collected` restringe
  à amostra do manifesto. Nenhum relatório imprime Riot ID, PUUID, hash de jogador,
  `matchId` ou chave.
- **Viés e limites.** A amostra é de conveniência: as contas se sobrepõem, um jogador
  aparece em muitas partidas e amostra grande não é amostra representativa. Sem MMR nem
  elo. `Retry-After` real e `count` acima de 100 não foram observados. Os dados coletados
  não entram no Git.

## Migrações

```sh
uv run alembic upgrade head        # aplica migrations pendentes (idempotente)
uv run cc db upgrade                # equivalente via entrypoint cc
uv run cc db current                # mostra a revisão atual do banco configurado
```

Requer `RIFTLAB_DATABASE_URL` apontando para um PostgreSQL real (SQLite não é
suportado). Em produção/dev via Compose: `docker compose run --rm api db upgrade`
(o entrypoint da imagem já é `cc` — não repetir o nome do comando).
