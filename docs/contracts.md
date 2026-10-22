# Contratos de aplicação

## Analisar e praticar como experiências distintas

Na decisão elegível, `Analisar partida` é a apresentação padrão e cria uma análise normal por `POST /analyses` com objetivo BALANCED, chave idempotente estável por montagem e timeout de 20 s. Não cria learning-attempt, resposta, pista, revelação ou progresso fictício. A UI identifica equilíbrio como padrão automático da análise, não escolha humana. Usa contratos/regras/modelos/abstenções existentes; uma alternativa ordenada não é validada como melhor escolha estratégica. Exportação normal sem attemptId; objetivo alternativo e cenário continuam disponíveis.

`Praticar decisão` é explícito por `?mode=practice`; mantém StudyPanel e a ocultação até revelação. URL pode ser compartilhada nessa modalidade. Troca de modo cancela a requisição pendente e reinicia o estado visual, sem apagar registros já persistidos; resultado tardio não aparece no modo oposto. A exposição anterior à análise pode influenciar a prática, por isso ela não é avaliação independente dessa decisão e o usuário é orientado a usar outra. Não se alteram gates científicos nem se alega aprendizagem demonstrada.

## Nomes de itens na interface

Análises de patch sem confirmação própria (17/10/2026): `support.reasonCodes` inclui `RECONSTRUCTION_RULES_INHERITED` quando o estado usou regras herdadas e `ITEM_RULES_INHERITED` quando a legalidade veio do manifesto de outro patch (`versions.itemRules` = `<patch>.json (herdado)`); com qualquer um deles, `support.level` é `LIMITED`. Sem manifesto próprio nem herdável, continua `UNSUPPORTED` com `PATCH_UNSUPPORTED`.

`GET /api/v1/decisions/{id}` expõe `versions.dataDragonVersion`, opcional/nulo quando não há catálogo, lido da versão imutável identificada por `catalogId`, não do catálogo canônico corrente. Catálogos sintéticos preservam seu identificador próprio; a interface não tenta buscá-los no CDN. A consulta permanece somente leitura, sem rede e sem escrita.

Análises com popularidade em patch sem referência própria podem usar a célula do patch numérico anterior mais recente somente quando a identidade semântica do catálogo de origem (hash de conteúdo, mapping, curadoria e proveniência) está vinculada por manifesto companion aos hashes exatos do artefato e dataset. As contagens são filtradas pelo diff estrutural CC-801 contra o catálogo imutável fixado na decisão; o prior é recalculado a partir das contagens retidas, sem redistribuição. A política histórica é suporte maior que zero; o limiar de 20 decisões é reservado a novas referências próprias da Parte B do CC-803. A API preserva `engine=POPULARITY`, marca `support.level=LIMITED`, inclui `POPULARITY_INHERITED` e descreve `support.referenceSource` (`type`, patch, versão e hashes/proveniência disponíveis). `catalogId`, quando presente, é identificador local adicional; a resolução entre instalações usa o hash semântico exato, nunca o UUID como substituto nem o catálogo canônico mais recente. Sem manifesto hash-bound ou sem catálogo com identidade correspondente, a herança falha fechada para `RULES` e informa a causa. O artefato histórico `popularity-16.19-v2.json` permanece byte a byte inalterado; o vínculo vem do manifesto `popularity-16.19-v2.provenance.json` (`schemaVersion=1`, `artifactSha256`, `datasetSha256`, `sourceCatalogIdentity`), reconstruído depois da exportação a partir do catálogo fixado por hash na remedição que gerou as linhas do dataset. `versions.ranking` recebe o sufixo ` (herdado)` quando a célula é herdada e o resultado cabe no limite de 40 caracteres; `POPULARITY_INHERITED` marca a herança em qualquer caso. A UI exibe patch e origem, sem converter frequência em recomendação estratégica.

A interface consulta `https://ddragon.leagueoflegends.com/cdn/{versão}/data/pt_BR/item.json` apenas para versões numéricas completas validadas, sem credenciais nem referrer, com limite de dez segundos. Valida a versão da resposta, usa apenas nomes textuais por ID e compartilha a consulta por versão na sessão. Trocas de versão nunca reaproveitam nomes antigos. Resposta inválida/indisponível preserva o nome da API ou o ID; falhas não são mantidas no cache. A tradução abrange inventário, amuleto, alternativas/comparação/cenários e padrões por campeão; não reescreve pistas já persistidas nem relatórios, dados, atributos, receitas ou resultados de ranking. O idioma é pt-BR fixo nesta entrega, inclusive para navegador em inglês.

## Padrões por campeão (CC-307)

`GET /api/v1/champions/patterns` é uma consulta descritiva em transação READ ONLY, sem coleta, extração, escrita ou uso dos arquivos de datasets. Parâmetros: `championId` inteiro positivo opcional, `patch` no formato `major.minor`, `phase=first|second|sequence` (padrão `sequence`) e `role=BOTTOM` (única função suportada). Resposta `ChampionPatterns`, tipada no OpenAPI; parâmetros inválidos retornam 422.

O recorte vem da configuração vigente e da janela de patches detectada no banco. A lista de campeões inclui contagem de partidas importadas no patch/função e nomes/retratos do catálogo fixado. A amostra detalhada limita-se às 200 participações mais recentes do campeão selecionado. Pontos devem corresponder à reconstrução corrente, ao catálogo da marca e às versões atuais de seleção, features, recorte, classificação e curadoria. DEMO não entra nos agregados reais.

`observedDecisions` conta compras principais reconstruídas nas ordens selecionadas; `eligibleDecisions` é o subconjunto elegível. `patternDenominator` conta participações com exatamente uma compra elegível por ordem exigida, em ordem temporal crescente. Uma sequência nunca é completada com eventos incertos ou ausentes. Os seis padrões mais frequentes mantêm o denominador completo; nomes e versões dos itens vêm do catálogo da respectiva reconstrução.

Estados: `EMPTY` sem participações/janela detectada, `OUT_OF_SCOPE` para campeão/patch não suportado, `NO_ELIGIBLE` sem decisões elegíveis, `SMALL_SAMPLE` quando há decisões mas menos de cinco trajetos completos, `AVAILABLE` a partir de cinco trajetos completos. Frequências só aparecem em `AVAILABLE`; situações individuais elegíveis podem aparecer nos demais estados de pouca amostra. Esse mínimo é uma regra de apresentação, não suporte estratégico validado. `situations` contém até seis referências a decisão/partida/participante/instante, sem alvo histórico, features ou identidade de conta. A jornada de estudo existente controla a revelação por situação.

As observações históricas agregadas não alteram baseline, treino, teste reservado ou candidatos legais de uma decisão. O guia não afirma eficácia, vitória ou build ideal e não oferece recomendação por frequência.

## Patch vigente e suporte (CC-314)

`cc patch detect` lê somente `versions.json` público e grava o patch vigente,
data e hash no banco configurado. A preparação e `cc catalog sync` resolvem
um patch ausente dos overrides por `major.minor`: escolhem a primeira versão
publicada para aquele minor na lista do Data Dragon (a última ocorrência na
lista decrescente), e gravam versão, data, hash e regra. Uma resolução gravada
não muda; overrides explícitos têm precedência. Sem correspondência, a
preparação marca `MISSING_CATALOG`, que pode ser tentado novamente, sem usar
`latest` de outro patch. Nenhum GET acessa a rede.

`configs/scope.yaml` mantém o registro histórico 16.16/16.17 e declara uma
janela móvel de dois patches. A detecção explícita é obrigatória para esse
formato. Após detecção, coleta, extração e relatórios usam
o vigente e o anterior observados no mesmo `versions.json`; versões de
extração e manifesto de coleta guardam a janela efetiva. A API de partida
publica `supportLevel: LIMITED` e `supportReason: PATCH_NOT_VALIDATED` para
partidas RIOT sem avaliação de ranking por patch. DEMO é ilustrativo. Nenhum
ranking é criado por esta informação.

`cc report patch-readiness` lê apenas agregados por patch. `INSUFFICIENT`
indica que nem o limite superior de participantes ou o número mínimo de
partidas alcança o protocolo CC-207; `NEEDS_UNIT_AUDIT` exige contagem das
unidades específicas e novo pré-registro antes de qualquer confirmação.
Patch vigente sem partida aparece explicitamente com zero.

Especificação para implementação. Prefixo /api/v1, JSON camelCase; IDs opacos UUID exceto match ID. Erro application/problem+json com type, title, status, code, detail seguro e requestId. Listas têm items, nextCursor, limit padrão 20/máximo 100, ordenação estável por timestamp/id. Desconhecido não vira zero.

**Estado real (13/10/2026).** Todas as rotas da matriz abaixo marcadas como **Implementado** estão montadas e descritas no OpenAPI versionado (`backend/openapi.json`, gerado por `cc openapi export`). O drift é travado por `tests/contract/test_openapi_contract.py` (o conjunto de caminhos é exatamente o das rotas reais) e, no frontend, por `npm run check:api-types`. São elas:
- `/health/*`;
- importações;
- partidas: lista, detalhe, preparação, decisões, prévia e expurgo;
- decisões;
- `/ranked/me`;
- `/history/sync` (CC-901);
- `/matches/{id}/review` (revisão da partida, CC-1003);
- `/players/*` (perfil de jogador por Riot ID, CC-1101);
- análises, com julgamento e relatório;
- tentativas de estudo;
- `/objectives/interpret`.

Os DTOs ficam em `backend/src/riftlab/shared/api/`. O envelope de erro (`application/problem+json`) é único, com códigos 409 específicos (IMPORT_ACTIVE, IDEMPOTENCY_CONFLICT, ATTEMPT_LOCKED, ATTEMPT_NOT_REVEALED, HINTS_DISABLED, FEEDBACK_EXISTS, MATCH_PURGED).

Importação real (CC-201): job PostgreSQL durável com token de posse
(`import_jobs.lease_generation`, incrementado a cada reserva `SELECT ... FOR
UPDATE SKIP LOCKED`) e link de retry (`retry_of_job_id`) — ver
`docs/domain-model.md`. Client Match-V5/Timeline-V5 real
(`ingestion/infrastructure/riot_source.py`) só aceita partida com
`endOfGameResult=GameComplete` (RF17: nunca uma fonte "ao vivo"); `puuid`
nunca persistido bruto, só `subject_hash` via HMAC-SHA256 com
`RIFTLAB_SUBJECT_HASH_SECRET` (obrigatório em modo RIOT, ausente falha de
forma segura, nunca degradado para hash simples). Corpos brutos
match+timeline ficam num armazenamento durável próprio
(`ingestion/infrastructure/raw_store.py`, gravação atômica
temp+`os.replace`), distinto do manifesto JSON do spike CC-105 (nunca
alterado), para CC-203/204 reconstruírem sem nova coleta.

## Matriz rota/contrato/estado

| Rota ou comando | Schema (backend/src/riftlab/shared/api/) | Estado | Card responsável |
| --- | --- | --- | --- |
| GET /health/live, /health/ready | `LiveStatus`/`ReadyStatus` (`api/routes/health.py`) + `ProblemDetail` (`errors.py`) | **Implementado** | CC-101, OpenAPI fechado no CC-106 |
| `cc db upgrade`/`db current` | — (Alembic) | **Implementado** (CLI) | CC-103 |
| `cc riot audit-sample` | — (CLI administrativa) | **Implementado** (CLI, spike) | CC-105 |
| `cc openapi export` | — (CLI) | **Implementado** (CLI) | CC-106 |
| `DemoMatchSource`/`DemoCatalogSource` | `MatchSourceResult`/`CatalogSourceResult` (`ingestion/domain.py`, `catalog/domain.py`) | **Implementado** (não é rota HTTP) | CC-104 |
| POST /api/v1/imports, GET .../{id}, POST .../retry | `imports.py` (`ImportRequest`, `ImportJobAccepted`, `ImportJobStatus`) + `RiotMatchSource`/`DemoMatchSource` + worker (`ingestion/application.py`) | **Implementado** | CC-201 |
| `cc catalog sync` | — (CLI) + `DemoCatalogSource`/`DataDragonCatalogSource` + `catalog/application.py` | **Implementado** (CLI, não é rota HTTP) | CC-202 |
| `cc timeline reconstruct` | — (CLI) + `timeline/application.py:reconstruct_match`/`reconstruct_participant_purchases` | **Implementado** (CLI, não é rota HTTP) | CC-203 |
| `cc rules health [--patch PATCH]` | — (CLI) + `timeline/health.py:compute_health` | **Implementado**: JSON agregado; sem opção recalcula patches com partidas, `--patch` limita a um patch. Mede por patch/fila/aplicação/origem/fingerprint/versão; invalidação é distinta de medição real e a medição real mais recente encerra retenção de REJECT | CC-801 |
| `cc matches backfill-final-items --match-id BR1_... --raw-dir PATH` | — (CLI) + `ingestion/final_items_backfill.py` | **Implementado**: exige `RIFTLAB_BACKFILL_DATABASE_URL` explícita; lê raw local íntegro, sem rede nem `.env` implícito; confere hash/identidades, insere apenas fatos ausentes e bloqueia divergência. Resultado `UPDATED`, `UNCHANGED` ou `RAW_ABSENT`; raw inválido bloqueia sem escrita | CC-801 |
| `cc report catalog-diff --from PATCH --to PATCH` | — (CLI) + `catalog/diff.py` | **Implementado**: diferenças em JSON agrupadas por motivo; catálogo ausente gera `CATALOG_UNAVAILABLE` e exit 1. Compara presença, receita, classe e flags `purchasable`, `maps`, `requiredChampion`; preço não é comparado | CC-801 |
| `cc decisions extract --match-id X` | — (CLI) + `decisions/application.py:extract_decisions` sobre `decision_extractions`/`decision_points` (imutáveis, versionadas) | **Implementado** (CLI, só banco: nunca reconstrói, abre fonte nem acessa rede; não é rota HTTP; Rodada 3 do CC-204, local) | CC-204 |
| `cc collect run\|report\|import`, `cc report reconstruction\|diversity\|integrity` | — (CLI) + `collection/` (sementes permitidas, manifesto durável, filtros, coletor, importação a partir do raw local) + `reports/` + `riot_rate_limiter.py` | **Implementado** (CLI, não é rota HTTP; Rodada 1 do CC-206, local; ensaiado só com servidor Riot falso, nenhuma coleta real): saídas só com agregados, rótulos pseudônimos e códigos seguros; o worker passou a importar job RIOT pelo raw local quando o par está completo (sem chave, sem cliente HTTP, sem cooldown de rede) | CC-206 |
| `classify_catalog` (classificação de item principal, `item_classification_v1`) | — (função pura em `catalog/classification.py`) sobre a curadoria `data_dragon_v5` (`attributes.into`) | **Implementado** (biblioteca, não é rota HTTP; Rodada 1 do CC-204, local) | CC-204 (reutilizado por CC-301) |
| GET /api/v1/matches, GET /api/v1/matches/{id}, POST /api/v1/matches/{id}/preparation | `matches.py` (`MatchSummary`, `MatchDetail`, `MatchParticipant`, `MatchPreparation`) + `enums.PreparationStatus` + `api/routes/matches.py` + `matches/queries.py` + `preparation/application.py` | **Implementado** (CC-205/801): as leituras consultam o banco; em `MatchParticipant`, `finalItems: int[] | null` e `finalTrinketItemId: int | null` expõem fatos oficiais quando conhecidos. `finalItems: []` é inventário normal observado vazio; `null` indica ausência do fato. `finalTrinketItemId: null` com `finalItems` conhecido indica ausência observada de amuleto. `roleBound` e hash bruto não são expostos. Sem fato, o placar usa a reconstrução por eventos identificada como estimativa. POST agenda preparação local, sem nova coleta | CC-201/205 |
| Campeões por patch (CC-208): `champion` em `MatchParticipant` (`ChampionAsset`: `name`, `dataDragonVersion`, `imageFile`, ou null) | `champions/` (`ensure_champion_catalog` com rede só na preparação/CLI e só para patch mapeado; `champion_assets` só banco), tabelas `champion_catalogs`/`champions` (migration `d6a1f3b9c2e7`), `DataDragonTransport.fetch_champion_catalog` (rota fixa, idioma `pt_BR`) | **Implementado** (CC-208): DEMO sempre null; falha do CDN não falha a preparação; retrato montado no navegador só com host oficial e padrões validados | CC-208 |
| Preparação automática (worker): import bem-sucedido → catálogo → `reconstruct_match` v7 (regras empíricas do registro; todas `PENDING` ⇒ comportamento da v6) → `extract_decisions` | `import_jobs.kind=PREPARE` (migration `c5e9a2d7f1b8`) + `preparation/application.py:process_next_preparation` no laço do `cc worker` | **Implementado** (CC-205, Rodada 1): mesma maquinaria de lease/token de geração/backoff do CC-201; no máximo uma preparação ativa por partida (índice único parcial); idempotente pelas marcas de reconstrução e extração | CC-205 |
| GET /api/v1/matches/{id}/decisions (`participantId` obrigatório), GET /api/v1/decisions/{id} | `matches.py` (`DecisionStateSummary`, `DecisionState`, `DecisionReason`, `DecisionInventory`, `InventoryItem`, `DecisionBudget`, `ObservedLevel`, `DecisionVersionInfo`) + `api/routes/decisions.py` + `decisions/queries.py` | **Implementado** (CC-204/801): só leitura; nunca reconstrói/extrai/abre fonte/usa rede. `DecisionStateSummary` expõe apenas `reconstructionRulesHealth` (OK/UNVERIFIED/SUSPENDED), `reconstructionRulesVerdict` (ACCEPT/REJECT/NO_SAMPLE) e `reconstructionRulesWilsonUpper`. No detalhe, `versions` também informa `reconstructionRules` (VALIDATED/INHERITED/NONE), `reconstructionRulesPatch`, `reconstructionRulesQueue` (420/440 quando a confirmação de origem vale só para uma fila, CC-802; `null` para o patch inteiro), `reconstructionRulesHealth`, `reconstructionRulesVerdict`, `reconstructionRulesWilsonUpper`, `reconstructionRulesCleanParticipants` e `reconstructionRulesFailures`. São indicadores observacionais: ACCEPT não promove herança nem substitui confirmação própria. | CC-203/204 |
| POST /api/v1/objectives/interpret | `objectives.py` (`ObjectiveInterpretRequest/Result`, `ObjectiveForm`) + `api/routes/objectives.py` + `objectives/` | **Implementado** (CC-504): provedor RULES (padrão, `rules_v1`, sem modelo) ou LOCAL_MODEL (sidecar em loopback, `RIFTLAB_OBJECTIVE_PARSER=local_model` + `RIFTLAB_OBJECTIVE_MODEL_URL`); saída do modelo validada estritamente contra `configs/objective-schema.json`; `status` INTERPRETED ou NEEDS_INPUT com `reasonCodes` (AMBIGUOUS_OR_OUT_OF_SCOPE, INVALID_MODEL_OUTPUT, TIMEOUT, PROVIDER_ERROR); `provider` rastreável; timeout `RIFTLAB_OBJECTIVE_PARSER_TIMEOUT_SECONDS` (15 s); texto não persistido nem registrado. `ObjectiveForm.parserVersion` opcional marca objetivo vindo de texto confirmado e só rastreia. Gate (CC-503/506): o padrão só pode ser versão aprovada em `configs/objective-parser-gate.yaml` (hoje RULES/`rules_v1`; nenhum modelo ajustado aprovado), travado por `tests/unit/test_objective_gate.py` | CC-501-506 |
| POST /api/v1/analyses, GET .../{id} | `analyses.py` (`AnalysisRequest`, `AnalysisResult`) + `api/routes/analyses.py` + `analyses/application.py` + `ranking/engine.py` | **Implementado** (CC-303): candidatos só do gerador legal; motor POPULARITY (contagens agregadas do treino 16.19, `configs/ranking/popularity-16.19-v1.json`) ou RULES (`configs/ranking/rules-v1.yaml`); objetivo reordena de forma explícita (`objective-v1.yaml`) e `baseRank` preserva a ordem-base; justificativas com campo de origem; resultado persistido e imutável (gatilho); Idempotency-Key obrigatória (mesmo corpo 200, divergente 409 IDEMPOTENCY_CONFLICT); patch sem regras de itens → UNSUPPORTED; ponto inelegível ou sem candidato legal → INSUFFICIENT_EVIDENCE com motivos e sem itens. CONTEXTUAL_RANKER não é servido (nenhum modelo superou o baseline). `versions.registryVersion` é a versão ativa do registro, e `versions.registryFallback` diz por que não há versão utilizável (NO_ACTIVE_VERSION, ou ACTIVE_ARTIFACT_HASH_MISMATCH quando o arquivo do modelo ativo foi alterado; CC-601) | CC-301-304, CC-401-405, CC-601 |
| POST /api/v1/analyses/{id}/feedback, GET .../report | `feedback.py` (`FeedbackRequest`, `FeedbackResult`, `ReportQuery`) + `reviews/` | **Implementado** (CC-505): julgamento único por (análise, item, hash do pseudônimo, rubrica), imutável (gatilho), mesma chave/corpo 200, corpo divergente 409 IDEMPOTENCY_CONFLICT, julgamento diferente para a mesma tupla 409 FEEDBACK_EXISTS, item fora das alternativas 422; pseudônimo nunca devolvido (`reviewerRef` de 12 hex). Relatório `format=json|html` com `attemptId` opcional (de outra análise → 422): sem matchId/participantId/PUUID/pseudônimo, Riot ID e tokens tipo chave removidos do texto livre, HTML escapado, sem script, CSP `default-src 'none'`, `Content-Disposition: attachment` | CC-505 |
| DELETE /api/v1/matches/{id}?confirm=, GET .../purge-preview | `purge.py` (`PurgePreview`) + `imports.py` (`ImportJobAccepted`, `ImportJobStatus` `kind=PURGE`) + `ingestion/purge.py` | **Implementado** (CC-603): `confirm` igual ao id (senão 422); 202 com job PURGE em `/imports/{jobId}` (`matchId` sempre nulo); idempotente, inclusive depois de concluído (pela lápide); worker apaga brutos e depois, numa transação, dependentes em ordem de FK, invalida datasets que listam a partida e retira modelos ativos (evento INVALIDATE), grava lápide com HMAC e apaga o id da linha do job; reimportação RIOT de partida expurgada → 409 MATCH_PURGED; `cc retention run` só lista sem `--execute` | CC-603 |
| POST /api/v1/history/sync, GET /api/v1/history/sync | `history_sync/api.py` (`HistorySyncStatus`) + `history_sync/application.py` + passo do worker | **Implementado** (CC-901): a requisição só agenda; o worker resolve a conta própria, lista IDs ranqueados recentes e enfileira IMPORT RIOT das partidas novas | CC-901 |
| POST /api/v1/learning-attempts e sub-rotas | `learning.py` (`LearningAttempt*`, `LearningProgress`) | **Implementado** (CC-304; CC-505 acrescenta `mode` STUDY/TRANSFER — TRANSFER recusa pistas com 409 HINTS_DISABLED —, `POST .../{id}/reflection` uma vez após REVEALED (antes: 409 ATTEMPT_NOT_REVEALED; texto diferente: 409 ATTEMPT_LOCKED) e `GET .../progress` com contagens locais descritivas) | CC-304, CC-505 |

**Fronteira HTTP (decisão D2 do CC-204, fixada em 19/09/2026):** naquela decisão, o CC-204 delimitou `GET /api/v1/matches/{id}/decisions` (`participantId` obrigatório, paginação por cursor) e `GET /api/v1/decisions/{id}`; o CC-205 acrescentou `GET /api/v1/matches`, `GET /api/v1/matches/{id}` e `POST /api/v1/matches/{id}/preparation`. As rotas listadas na tabela acima são as montadas atualmente. Nenhum GET dispara rede, reconstrução, extração ou mutação: consultam só estados já preparados. Cursor e escopo (partida, participante, versões, limite de visibilidade da primeira página) são vinculados ao contrato; cursor inválido ou de outro escopo gera erro seguro.

Nenhum schema-only está montado como rota FastAPI; importar `riftlab.shared.api` não expõe endpoint algum (`tests/contract/test_openapi_contract.py::test_openapi_only_announces_real_routes` trava isso). Campos sem enumeração fechada nos documentos (ex.: `support.level`, `reasonCodes`, o `status` de sucesso de `objectives/interpret`) foram deixados como string validada, não como enum fechado, para não inventar um fechamento que os documentos de contrato não afirmam — ver comentários em cada schema.

| Método e rota | Entrada | Saída e semântica |
| --- | --- | --- |
| GET /health/live, /health/ready | nenhuma | 200; readiness 503 se banco indisponível |
| POST /api/v1/imports | {matchId, mode: RIOT ou DEMO} | 202 {jobId,status,statusUrl}; mesmo job ativo reaproveitado; 422 VALIDATION_ERROR (payload) ou 422 RIOT_KEY_MISSING (mode=RIOT num servidor sem chave da Riot; nenhum job criado; CC-205) |
| GET /api/v1/imports/{id} | UUID | 200 {status,attempts,errorCode,retryAt,matchId} |
| POST /api/v1/imports/{id}/retry | job FAILED | 202; 409 se ainda ativo; nova tentativa auditável |
| GET /api/v1/matches | cursor (opaco), limit (padrão 20, máx. 100) | CursorPage de `MatchSummary` {matchId, queue, map, patch, startAt, durationSeconds, preparationStatus, championsPresent: [{championId, name\|null}], participants: [`MatchParticipant`], localParticipantId\|null, reviewPurchases {ready, total}\|null (19/10/2026, CC-1005)}: `championsPresent` e `participants` cobrem **qualquer participante**; `localParticipantId` (17/10/2026) é só a posição da conta local configurada, resolvida como no Painel (observação League-V4 da conta e comparação do hash dentro do banco; DEMO: participante 1), e fica null sem conta, sem observação ou com correspondência ambígua; só partidas importadas, ordem estável `startAt` decrescente e desempate `matchId` decrescente (keyset: partida nova não desloca um cursor em andamento); sem PUUID/subject_hash; leitura só do banco e catálogo de versão fixada; 422 VALIDATION_ERROR/INVALID_CURSOR (CC-205/308) |
| GET /api/v1/matches/{id} | match ID | `MatchDetail`: resumo acima, `participants` [{participantId, championId, team, role\|null, champion\|null, win\|null, kills\|null, deaths\|null, assists\|null, totalMinionsKilled\|null, neutralMinionsKilled\|null}] `preparation` {status, errorCode, retryAt, attempts} e `localParticipantId` (mesma regra da lista); sem identificador pessoal; 404 desconhecido (CC-205/208/308/313) |
| GET /api/v1/matches/{id}/timeline | match ID | `MatchTimeline` (17/10/2026) {matchId, dataDragonVersion\|null, participants [{participantId, items [{timestampMs, itemId, kind PURCHASE\|SOLD, itemClass\|null}], skills [{timestampMs, skillSlot 1..4, levelUpType}]}], kills [{timestampMs, killerId, victimId, assistingIds}], objectives [{timestampMs, kind, team\|null, subType\|null}], goldFrames\|null}: desfazer já aplicado, participante 0 ignorado, `team` do objetivo é quem conquistou (torre: o oposto do dono), `goldFrames` null até a preparação gravar os quadros; só banco; 404 desconhecido |
| GET /api/v1/matches/{id}/review?participantId=N | match ID + participante 1..10 | `MatchReview` (19/10/2026): compras do 1º ao 5º item principal com `status` READY\|NOT_READY e, prontas, `label` COMMON\|ALTERNATIVE\|UNCOMMON\|NO_REFERENCE, `share`, `rank`, `support` e `referenceTop` (3 mais comuns); `skills` (prioridade de nível máximo × referência); `lane` (ouro, XP, farm e nível aos 10 e 15 min contra o oponente direto); `winProbability` (série por minuto do time do participante e até 3 `keyMoments`) ou `winProbabilityUnavailable` MODEL_UNAVAILABLE\|MODEL_GATE_FAILED\|FRAMES_WITHOUT_XP; `counts`; `referenceInherited` quando o patch ou a fila diferem da referência; só banco; 404 desconhecido |
| POST /api/v1/players/lookup, POST/GET /api/v1/players/{id}/sync | {gameName, tagLine, refresh?} / id do perfil (+ {gameName, tagLine} no POST da sincronização, CC-1103) | `PlayerProfile` (20/10/2026) {id, gameName, tagLine (canônicos quando a Riot foi consultada agora; senão os do pedido), profileIconId, summonerLevel, refreshedAt, dataDragonVersion, solo\|null, flex\|null ({queue, state READY\|UNRANKED, tier, division, leaguePoints, wins, losses, observedAt}), staleReason}; sincronização com `HistorySyncStatus`; 404 PLAYER_NOT_FOUND/PLAYER_NOT_ON_PLATFORM, 422 INVALID_RIOT_ID, 503 RIOT_KEY_MISSING/RIOT_AUTH_FAILED/RIOT_RATE_LIMITED (com Retry-After)/UPSTREAM_UNAVAILABLE |

**Placar factual (CC-313).** Os seis campos de placar do detalhe pertencem a **cada participante**, não à conta própria. `null` significa que a fonte ou a linha legada não fornece valor confiável; zero é observação explícita. `win` é booleano observado no Match-V5 (partida com `endOfGameResult=GameComplete`) ou na fixture DEMO, nunca inferido do elo ou do time. `totalMinionsKilled` e `neutralMinionsKilled` são armazenados separadamente. Esta API não publica uma soma de tropas; se a interface vier a exibir uma contagem combinada, sua fórmula será `totalMinionsKilled + neutralMinionsKilled` apenas quando ambos forem conhecidos. Nenhum campo final entra nas features pré-decisão. `cc matches backfill-score --match-id BR1_... --raw-dir PATH` exige `RIFTLAB_BACKFILL_DATABASE_URL` explícita, lê raw local íntegro (inclusive formato legado), verifica hash/identidade e só preenche nulos sem sobrescrever valor divergente; não abre rede nem `.env` implícito.
| POST /api/v1/matches/{id}/preparation | match ID (sem corpo) | nova tentativa de preparação: 202 `MatchPreparation` (PENDING) se enfileirou ou já havia uma ativa; 200 READY se já preparada com as versões correntes; 404 partida inexistente/sem importação concluída; 422 matchId inválido. Idempotente; histórico preservado; nunca chama a Riot (CC-205) |
| GET /api/v1/matches/{id}/decisions | participantId (obrigatório, 1..10), cursor (opaco), limit (padrão 20, máx. 100) | CursorPage de resumos por ordem estável (timestampMs, eventSourceIndex, id); 404 partida/participante; 409 DECISIONS_NOT_PREPARED; 422 VALIDATION_ERROR/INVALID_CURSOR (CC-204) |
| GET /api/v1/decisions/{id} | UUID do PONTO de decisão (qualquer extração, mesmo antiga) | inventário anterior (multiconjunto + trinket; cada item com `name` lido do catálogo FIXADO pelas versões do ponto, `null` sem catálogo ou item sem nome; nunca o canônico atual), orçamento, nível observado, motivos e versões (`versions.reconstructionRules` VALIDATED\|INHERITED\|NONE e `reconstructionRulesPatch`, o patch de origem das regras da reconstrução v10); 404 desconhecido; 422 UUID inválido (CC-204) |
| POST /api/v1/objectives/interpret | {text} até 500 caracteres | {status: INTERPRETED\|NEEDS_INPUT, objective, parserVersion, provider, reasonCodes}; NEEDS_INPUT quando ambíguo, fora do domínio, instrução ao interpretador, saída inválida, timeout ou falha |
| POST /api/v1/analyses | {decisionId,objective,budget?} + Idempotency-Key | 201 análise persistida; repetição idêntica 200, corpo divergente 409 |
| GET /api/v1/analyses/{id} | UUID | resultado imutável |
| POST /api/v1/analyses/{id}/feedback | {itemId,grade,reason,rubricVersion,reviewerAlias} + Idempotency-Key | 201/200; grade 0..3 ou null para abstenção |
| GET /api/v1/analyses/{id}/report | format=json ou html | exportação sem identificadores pessoais |
| DELETE /api/v1/matches/{id} | `confirm` = o próprio id (query) | 202 `ImportJobAccepted` do job PURGE, consultável em /imports/{jobId} (`kind=PURGE`, `matchId` nulo); expurgo ativo ou já concluído devolve o mesmo job; 404 partida desconhecida; 422 sem confirmação ou diferente (CC-603) |
| GET /api/v1/matches/{id}/purge-preview | match ID | `PurgePreview` {counts, datasets, models, unverifiableDatasets, limitations}; nada é alterado (CC-603) |
| POST /api/v1/history/sync | nenhuma | 202 `HistorySyncStatus` QUEUED quando cria a execução; 200 com a execução ativa, a última dentro do intervalo mínimo ou `UNAVAILABLE` (sem chave ou sem conta configurada). Nenhuma chamada à Riot na requisição |
| GET /api/v1/history/sync | nenhuma | 200 `HistorySyncStatus` da última execução da conta própria (`IDLE` se nunca houve); só banco |


**Fechamento observacional e estados de saúde (CC-801).** A sentinela G-FINAL compara slots normais e amuleto contra fatos oficiais hash-verificados, somente para participantes de reconstrução certa; não usa `roleBound`, itens concedidos, nem quantidades de consumíveis. Limiares: limite superior do intervalo de Wilson bilateral de 95% (`z=1,96`), `ε=0,10`, mínimo 20 participantes certos e 3 partidas contribuintes. `NO_SAMPLE` significa evidência insuficiente, não aprovação. Em `clean=0`, o veredito é `NO_SAMPLE` e `wilsonUpper=1.0`; `wilsonUpper: null` corresponde a invalidação ou ausência de medição. `ACCEPT` é monitoramento, não confirmação própria. Para regras herdadas, `REJECT` ou um `REJECT` vigente retido após invalidação bloqueia a análise com `INSUFFICIENT_EVIDENCE` e `INHERITED_RULES_SUSPENDED`; uma medição real `NO_SAMPLE` sem rejeição retida permite análise com aviso `INHERITED_RULES_UNVERIFIED`. Quando disponível, `RECONSTRUCTION_RULES_INHERITED` identifica a origem herdada. Herança de legalidade retorna `ITEM_RULES_INHERITED`. `VALIDATED` nunca é suspensa pelo veredito observacional. A leitura usa a linha da fila da decisão (420/440) e só recorre à linha geral quando a fila não tem linha (CC-804): `REJECT` do Flex suspende só o Flex e não é escondido por `ACCEPT` de outra população. Decisão Flex com regras confirmadas só em Solo/Duo e saúde do Flex `UNVERIFIED` recebe `RULES_MEASURED_IN_SOLO_DUO` em `support.reasonCodes`. Desde o CC-906, cada partida grava sua contribuição (participantes limpos e falhas por aplicação, origem, fingerprint e fila) em `match_rule_health_contributions`; o PREPARE refaz só a própria partida e as que ficaram desatualizadas (proveniência ou catálogo do marcador, ou número de itens finais oficiais diferente), e a saúde do patch é a agregação dessas linhas, com o mesmo veredito da recontagem completa. `cc rules health` continua recontando o patch inteiro; o expurgo apaga a contribuição junto com a partida.

Os fatos de `finalItems` são distintos da lista reconstruída de compras. `null` significa que o fato pós-partida não foi coletado/disponibilizado; `[]` significa que os slots normais foram observados vazios. Com `finalItems` conhecido, `finalTrinketItemId: null` indica ausência de amuleto. O backfill somente completa fato ausente a partir de raw íntegro e identidade/hash conferidos; divergência bloqueia e não sobrescreve. `cc rules health` recebe a opção opcional `--patch PATCH`; `cc matches backfill-final-items` exige `--match-id` e `--raw-dir`, além de `RIFTLAB_BACKFILL_DATABASE_URL`; `cc report catalog-diff` exige `--from PATCH` e `--to PATCH`. Estes são comandos CLI, não rotas HTTP.

Match ID inicial: ^BR1_[0-9]{1,20}$. Não aceitar host, URL ou caminho local de usuário. Formulário de objetivo: {priority: BALANCED|SURVIVAL|DAMAGE|UTILITY}. Budget opcional: {gold: inteiro 0..100000, source: USER_PROVIDED}; fonte de orçamento do servidor não é sobrescrita, cria cenário separado. Objetivos desconhecidos retornam 422. Textos são dados, não instruções de ferramenta; interpretador não tem acesso a shell, rede ou segredos.

## Resultado de análise

```json
{
  "analysisId": "UUID",
  "decisionId": "UUID",
  "status": "AVAILABLE",
  "engine": "CONTEXTUAL_RANKER",
  "objective": {"priority": "BALANCED"},
  "versions": {"model": "UUID", "catalog": "16.x.y", "features": "v1", "candidates": "v1"},
  "quality": {"budgetSource": "FRAME_ESTIMATE", "frameAgeMs": 30000, "warnings": []},
  "support": {"level": "LIMITED", "reasonCodes": ["SMALL_CONTEXT_SAMPLE"]},
  "candidates": [{"itemId": 100000, "rank": 1, "score": 0.41, "remainingCost": 1800, "affordability": "UNKNOWN", "evidenceCodes": ["OWNED_COMPONENT_COMPATIBLE"]}]
}
```

Exemplo ilustrativo de contrato, IDs e patch não são dados reais. status: AVAILABLE, INSUFFICIENT_EVIDENCE ou UNSUPPORTED. Sem evidência: candidates=[] e reasonCodes obrigatório. Decisão extraída com outra versão da reconstrução (partida preparada antes de a versão mudar) responde `INSUFFICIENT_EVIDENCE` com `STATE_VERSION_INCOMPATIBLE` (estado ilegível: `STATE_MALFORMED`), inclusive na tentativa de estudo, nunca 500 (CC-1207). engine: POPULARITY, RULES, CONTEXTUAL_RANKER. Score é relativo e não calibrado como probabilidade. Reason codes mínimos: MISSING_CATALOG, OUT_OF_SCOPE, INVENTORY_UNCERTAIN, STALE_FRAME, UNKNOWN_BUDGET, SMALL_CONTEXT_SAMPLE, OOD_CONTEXT, MODEL_UNAVAILABLE. Ausência do modelo retorna baseline explicitamente identificado se o estado é válido; estado inválido não ganha baseline como escape.

**Preparação de partida (CC-205).** `preparationStatus`/`preparation.status` ∈ {`NOT_REQUESTED` (partida sem job de preparação registrado), `PENDING` (na fila ou aguardando nova tentativa transitória: nesse caso `errorCode` traz a última falha e `retryAt` o próximo instante), `RUNNING`, `READY` (decisões das versões correntes extraídas; `items=[]` continua distinto de não preparado), `FAILED` (falha final; `errorCode` seguro: TIMELINE_*, MATCH_SOURCE_*, RAW_MATCH_NOT_CACHED, SUBJECT_HASH_SECRET_MISSING, CATALOG_MAPPING_FILE_MISSING, RETRY_LIMIT_EXCEEDED etc., nunca payload nem stack)}. O import bem-sucedido enfileira a preparação na mesma transação. Modo REAL: rede só para o catálogo público do Data Dragon, e somente quando o patch tem entrada em `configs/catalog-mappings.yaml` e ainda não há canônico; sem entrada não há rede e os pontos saem com MISSING_CATALOG. Erro estrutural → FAILED permanente; erro transitório (rede, 5xx, 429) → nova tentativa com backoff até `MAX_TRANSIENT_ATTEMPTS`. Caminho de nova tentativa pela API: `POST /api/v1/matches/{id}/preparation`. Enquanto a preparação não termina, `GET .../decisions` responde 409 DECISIONS_NOT_PREPARED.

Erros (matchId fora do padrão e demais parâmetros inválidos são 422 VALIDATION_ERROR, padrão vigente do projeto; não há código 400 específico de matchId): 422 RIOT_KEY_MISSING (CC-205: POST /imports com mode=RIOT quando o servidor não tem RIFTLAB_RIOT_API_KEY, isto é, está em modo demonstração; problem+json com texto fixo, sem menção a variável, caminho ou valor recebido; substitui o antigo HTTP_422 genérico; o frontend mostra a orientação "explore a demonstração ou configure a chave Development"), 409 DECISIONS_NOT_PREPARED (CC-204: partida existente, decisões da versão corrente ainda não extraídas; distinto de 200 com items=[] = extraída com zero pontos), 422 INVALID_CURSOR (CC-204: cursor malformado, adulterado nos tipos, de outra partida/participante ou de extração inexistente; nunca ecoa o cursor), 404 RESOURCE_NOT_FOUND, 409 IDEMPOTENCY_CONFLICT/IMPORT_ACTIVE, 422 VALIDATION_ERROR, 503 DATABASE_UNAVAILABLE. Falhas remotas ficam no job com RIOT_AUTH_FAILED, RIOT_RATE_LIMITED, RIOT_NOT_FOUND, UPSTREAM_UNAVAILABLE ou SCHEMA_UNSUPPORTED. Nunca devolver chave, stack trace, SQL ou corpo remoto bruto.

Restrição pedagógica das rotas de decisão (docs/learning-design.md: antes de revelar, só informação pré-decisão): PROIBIDO na resposta `targetItemId`/`targetClass`, `retrospectiveTarget`, vetor de features, qualidade cumulativa e `subject_hash`. Todo motivo ligado ao alvo (`TARGET_NOT_CLASSIFIABLE`, `TARGET_REVERTED`, `TARGET_STATUS_UNCERTAIN`) é publicado como o único código neutro `TARGET_NOT_ELIGIBLE`, sem sub-motivos (revelaria a classe do item ou que a compra foi desfeita); motivos de contexto (`MISSING_CATALOG`, `OUT_OF_SCOPE`, `INVENTORY_UNCERTAIN`) mantêm os sub-motivos literais. `cursor`: base64url de JSON versionado (v1) com extraction_id, match, participante e posição (timestamp, source_index, id); não assinado (adulterar a posição só troca o ponto de retomada dentro do mesmo escopo), validado estritamente; a página seguinte usa sempre a extração fixada no cursor

CLI prevista (implementar em S1–S6): cc catalog sync; cc ingest --manifest PATH; cc dataset build --config PATH; cc train ranker --config PATH; cc evaluate --manifest PATH; cc model promote ID; cc model rollback ID; cc demo seed. Treino usa caminhos locais do operador, não uploads públicos. Opções exatas e exemplos executáveis deverão ser atualizados após implementação.

## Contrato de aprendizagem (RF13–16)

- POST /api/v1/learning-attempts: {decisionId, objective} + Idempotency-Key → 201 {attemptId,status:OPEN,context,options,hintLevel:0}. options sem rank/score/evidence que revele ordem, sem compra histórica; ordem estável aleatorizada por tentativa. Chave repetida/corpo idêntico 200, divergente 409.
- GET /api/v1/learning-attempts/{id}: estado público da tentativa, preservando ocultação antes de REVEALED.
- POST /api/v1/learning-attempts/{id}/answer: {itemId ou null, rationale?, skipped:boolean} → 200 ANSWERED; se não skipped, item obrigatório; máximo 1000 caracteres. Reenvio igual idempotente, alteração após ANSWERED retorna 409 ATTEMPT_LOCKED; nova escolha requer nova tentativa.
- POST /api/v1/learning-attempts/{id}/hint: {level:1|2} → pista curada, registra maior nível acessado; não muda ordem nem revela ranking.
- POST /api/v1/learning-attempts/{id}/reveal: {explicit:true} → 200 REVEALED com analysisId/comparação; permite OPEN por pedido explícito, monotônico/idempotente. Criação da análise e atualização da tentativa atômicas quanto à persistência; inferência fora de transação longa.

Rotas de análise direta permanecem para revisão explícita; o fluxo Estudo usa tentativas e não solicita ranking no carregamento. Não é barreira antifraude contra o próprio operador local, mas separação intencional de contrato para não vazar resposta na UI. GET report pode receber attemptId vinculado à analysis para incluir aprendizado; tentativa de outra análise retorna 422. Toda justificativa é escapada e omitida de logs. Não pontuar resposta com rank como verdade absoluta.

## Histórico da conta configurada

`GET /api/v1/matches` aceita `scope=all|account|demo|account_and_demo` (padrão `all`,
preservando a consulta administrativa de importadas). O escopo `account` seleciona
somente partidas reais com participação da conta configurada, usando a mesma
identificação local de `localParticipantId`. Sem identificação, retorna uma página
vazia; nunca inclui contas de terceiros como alternativa. `demo` seleciona somente
partidas sintéticas, e `account_and_demo` reúne os dois conjuntos. A seleção acontece
no banco antes do limite e do cursor. O Histórico abre com `scope=account`; seus
filtros de origem mudam o escopo e reiniciam a paginação. Nenhum dado é apagado.

## CC-310 — Minha posição da conta local

Seleção inicial automática (16/10/2026): UI consulta ambas as filas em paralelo uma vez por montagem, com timeout de dez segundos. Classificação READY tem precedência sobre UNRANKED/ausência; entre classificações, observação com status READY precede STALE, seguida por tier, divisão e PDL. Empate mantém Solo/Duo; UNRANKED conhecido precede ausência. Nenhuma classificação é criada por inferência. A escolha manual é estado independente e nunca sobrescrita por resposta posterior; navegação desmonta e cancela as consultas. Observações ficam em memória durante a página e mudam após recarregar. Este comportamento substitui a consulta por troca de fila descrita na extensão original abaixo; a API e seu padrão Solo/Duo permanecem inalterados.

Extensão de filas (16/10/2026): parâmetro `queue=RANKED_SOLO_5x5|RANKED_FLEX_SR` em `GET /api/v1/ranked/me`, padrão Solo/Duo; valor inválido retorna 422. Leituras e histórico ficam isolados por conta, fila e temporada, inclusive erros e observações antigas. Atualização administrativa já coleta ambas as filas e agora registra falha em ambas quando a consulta comum falha, sem substituir posições válidas. Associação de partidas usa 420 para Solo/Duo e 440 para Flex; o intervalo não vira causalidade do PDL. Seletor nativo disponível na página e no card compacto; mudança de fila cancela a consulta anterior e não reaproveita a posição de outra fila. DEMO continua com Solo fictício, sem fabricar observações Flex. Os parágrafos originais abaixo descrevem o comportamento padrão Solo/Duo.

`cc ranked refresh --season YYYY[-ato] --account-file <arquivo-local>` resolve o PUUID pelo Account-V1 (Americas) e consulta `GET /lol/league/v4/entries/by-puuid/{encryptedPUUID}` em BR1 com a mesma chave. O arquivo de conta não entra na imagem; a API em container requer `RIFTLAB_RANKED_ACCOUNT_FILE` para um arquivo montado somente para leitura. A temporada é explícita porque o DTO League-V4 não a informa. `RiotRateLimiter` limita uma execução da CLI, não coordena processos separados. Falhas 401/403/429/rede/esquema geram tentativa `FAILED` sem apagar observação válida anterior; só resposta válida sem Solo/Duo gera `UNRANKED`.

`GET /api/v1/ranked/me` lê somente o banco e a identidade HMAC da conta local atual, sem parâmetro de conta, PUUID, Riot ID, chamada de rede ou ranking de terceiros. Retorna `READY`, `UNRANKED`, `UNAVAILABLE` ou `STALE`; em `STALE` preserva `position` e traz motivo. Ordenação de observações: `captured_at DESC, id DESC` (inclusive empate). Compara somente fila Solo/Duo e temporada idênticas. Delta numérico de PDL só na mesma tier/divisão e sem regressão dos contadores de vitórias/derrotas; mudança de tier/divisão vira `TRANSITION`, correção de contadores vira `CORRECTION`. Associação do intervalo a partida própria exige HMAC do PUUID da conta observada, fila 420 e exatamente uma partida concluída no intervalo aberto-fechado entre as observações. Mesmo quando há uma, a API não atribui causalidade do PDL à partida.

## CC-1101/CC-1102 — Perfil de jogador por Riot ID

Como em sites de estatísticas, qualquer Riot ID do servidor BR pode ser consultado. É **identificação, não login**: o perfil é público, e nada privado fica atrás dele.

- **Busca.** `POST /api/v1/players/lookup` normaliza o Riot ID: Nome com até 16 caracteres e sem `#`; TAG com 3 a 5 letras ou números. Depois chama a Riot, com 3 requisições e limitador único por processo:
  - Account-V1 pelo Riot ID, para o PUUID e o Riot ID canônico;
  - Summoner-V4 por PUUID em BR1, para o ícone e o nível;
  - League-V4 por PUUID, para o elo.

  O perfil fica em `player_profiles`, só com `account_key` (HMAC do Riot ID, a mesma da posição ranqueada), `subject_hash` (HMAC do PUUID, o mesmo dos participantes importados), ícone, nível e horário. O elo fica em `ranked_observations`. Nem o Riot ID nem o PUUID são gravados (CC-1103); o PUUID nunca é devolvido. A resposta traz o Riot ID canônico quando a Riot foi consultada agora e, no cache, o do pedido normalizado. Não há leitura do perfil por id.
- **Cache.** Perfil consultado há menos de 10 min é devolvido sem rede; `refresh` força a consulta. Sem chave da Riot, o perfil guardado volta com `staleReason=RIOT_KEY_MISSING`, e o perfil inexistente dá 503.
- **Histórico do jogador.** `POST/GET /api/v1/players/{id}/sync` usa o mesmo fluxo da CC-901. O POST traz `{gameName, tagLine}`, conferido pelo HMAC contra o perfil (outro Riot ID dá 404). A execução guarda o Riot ID só enquanto está `QUEUED`/`RUNNING` (restrição `history_sync_riot_id_active_only`), e o worker o apaga ao terminar. O worker lista as ranqueadas recentes e enfileira IMPORT das novas. O intervalo mínimo vale por conta. `GET /api/v1/matches?player={id}` lista só as partidas reais em que o jogador aparece, com o participante dele em `localParticipantId`.
- **Ícone.** A interface monta `https://ddragon.leagueoflegends.com/cdn/{dataDragonVersion}/img/profileicon/{profileIconId}.png`, com a versão do patch real mais recente do catálogo. Sem rede ou sem ícone, mostra a inicial do nome.
- **Retenção e remoção (CC-1103).** `cc retention run` apaga perfil, todas as observações de elo e as execuções terminadas de quem não foi consultado há `player_profile_retention_days` (7); a conta configurada fica de fora. `cc retention purge-player --game-name --tag-line [--execute]` faz o mesmo para um Riot ID; uma execução ativa fica até o worker terminá-la. As duas só imprimem contagens.
- **Interface.** A busca "Nome#TAG" fica no topo de todas as telas e leva a `/jogador/Nome-TAG`; o último hífen separa a TAG. A página mostra perfil, elo, o botão Atualizar (elo, ícone e partidas) e o histórico do jogador com os cartões do Histórico.

## CC-1210 — Riot ID dos participantes na partida

- `GET /api/v1/matches/{id}` traz `gameName` e `tagLine` em cada participante (`MatchDetailParticipant`), com o Riot ID como estava na partida. Os campos vêm do `info.participants[].riotIdGameName/riotIdTagline` da resposta bruta guardada pelo worker (`DurableRawMatchStore`), lida na hora do pedido. A API monta o volume `riftlab_raw_data` somente leitura e não grava nada.
- Ficam nulos em DEMO, quando o bruto não existe (por exemplo, partida expurgada), quando ele está corrompido (hash divergente) ou quando falta o Riot ID (partida antiga só com `summonerName`). Nome ou TAG vazio, com `#`, com caractere de controle ou com mais de 32 caracteres também ficam de fora.
- O PUUID do bruto nunca sai da API. A lista (`GET /api/v1/matches`), o Painel e o Histórico continuam sem Riot ID.
- Interface: no placar da partida, o Nome#TAG aparece embaixo do campeão como link para `/jogador/Nome-TAG`. Uma nota avisa que é o Riot ID da época da partida.

## CC-1211 — Partida ao vivo

- **Rota.** `POST /api/v1/players/live` com `{gameName, tagLine}`. É POST para o Riot ID ficar fora da URL e dos logs de acesso. Com chave da Riot, chama:
  - Account-V1, para o PUUID;
  - Spectator-V5 por PUUID em BR1, para a partida em andamento (404 da Riot vira `state=NOT_IN_GAME`);
  - League-V4 por PUUID para cada jogador humano, para o elo na fila da partida (Flex na fila 440; Solo/Duo nas demais).

  São até 12 requisições, com o limitador único do processo. Um 429 no elo encerra as consultas de elo e a partida volta assim mesmo, com `rankState=UNAVAILABLE` nos jogadores que faltaram.
- **Resposta.** `{state, gameName, tagLine, checkedAt, game}`. `game` traz `queueId`, `rankQueue`, `gameMode`, `mapId`, `startedAt` (nulo durante o carregamento), `lengthSeconds`, `dataDragonVersion` e dois times (`teamId` 100/200). Cada time tem:
  - `bans`, com o campeão em ordem de escolha;
  - `participants`, com o Riot ID como o jogo mostra na tela de carregamento (nulo para bot ou sem `#`), `requested` para o jogador buscado, campeão, `spell1Id`/`spell2Id`, `keystoneId`, `perkStyle`/`perkSubStyle`, `rankState` (`READY`, `UNRANKED`, `UNAVAILABLE`) e `rank`.

  Nunca há PUUID na resposta. Erros como na busca: `INVALID_RIOT_ID`, `PLAYER_NOT_FOUND`, `RIOT_KEY_MISSING`, `RIOT_AUTH_FAILED`, `RIOT_RATE_LIMITED` e `UPSTREAM_UNAVAILABLE`.
- **Nada gravado.** Nem banco nem disco. A resposta fica 20 s na memória do processo da API, por Riot ID, para "Verificar de novo" não repetir requisições.
- **Interface.** O botão "Partida ao vivo" no cabeçalho de `/jogador/Nome-TAG` leva a `/jogador/Nome-TAG/ao-vivo`. A tela mostra os dois times lado a lado (empilhados até 1100 px), o jogador buscado em destaque, o Riot ID de cada um com link para o perfil e o tempo de jogo correndo. Os ícones de feitiço e de estilo de runa vêm do Data Dragon; sem rede, aparece o nome.

## CC-1202 — Moldura: perfil da conta e catálogo atual

- **Perfil da conta.** `GET /api/v1/players/me` devolve o perfil da conta configurada no formato da busca, para o chip do topo. O Riot ID vem do arquivo da conta, nunca do banco. Com perfil guardado, responde só do banco, qualquer que seja a idade; o Atualizar da página do jogador renova. Sem perfil guardado, consulta a Riot uma vez, como a busca (3 requisições). A resposta é sempre 200 com `{profile, reason}`, como `/api/v1/ranked/me`: sem perfil, `profile` vem nulo e `reason` traz `ACCOUNT_NOT_CONFIGURED`, `SECRET_MISSING`, `RIOT_KEY_MISSING` ou o erro da consulta à Riot. Assim, uma instalação sem conta não registra erro no console a cada tela. A interface escolhe a melhor fila (elo, divisão e PDL) e, sem perfil, mostra só o selo BR1.
- **Catálogo atual.** `GET /api/v1/catalog/current` devolve `{gamePatch, dataDragonVersion}` do catálogo de patch real mais recente (formato do jogo, como `16.20`), ou os dois nulos. É a faixa "Patch · Data Dragon" abaixo do topo e a versão dos ícones de invocador.

## CC-1003–CC-1005 — Revisão da partida

`GET /api/v1/matches/{matchId}/review?participantId=N` é só leitura (transação READ ONLY). Usa as decisões da extração corrente, a linha do tempo, os quadros gravados na preparação e dois artefatos agregados e versionados em `configs/match-review/`:

- a referência `reference-16.19-solo-v1.json`, com contagens por campeão × rota × etapa e prioridade de habilidades por campeão × rota, sem identificador;
- o modelo `win-probability-16.19-v1.json`, com coeficientes, avaliação e gate.

Sem algum dos artefatos, a revisão sai sem a parte correspondente.

- **Compras.** Entram as compras do 1º ao 5º item principal do participante. Está pronta a compra cujos únicos motivos são de recorte (fila, patch, rota, campeão, etapa além da 2ª); qualquer incerteza do inventário deixa a compra `NOT_READY`. Os rótulos medem popularidade na mesma célula campeão × rota × etapa:
  - **COMMON:** entre as 3 mais escolhidas, com ≥ 10%;
  - **ALTERNATIVE:** ≥ 3%;
  - **UNCOMMON:** < 3%;
  - **NO_REFERENCE:** célula com menos de 20 jogadores.
- **Habilidades.** Ordem em que Q, W e E chegam ao nível máximo (subidas NORMAL). MOST_COMMON é a ordem mais comum da célula; ALTERNATIVE, ≥ 15%; UNCOMMON, o resto.
- **Fase de rotas.** Primeiro quadro em ou depois de 10 e de 15 min, contra o único participante da mesma rota no outro time. Sem oponente único, sai `lane` null.
- **Chance de vitória.** Mesmas variáveis do treino (`match_review/win_probability.py`), na perspectiva do time do participante. Só aparece com o gate do modelo aprovado e quadros com XP. Os momentos-chave são as maiores variações entre minutos seguidos (≥ 8 pontos percentuais), com os abates e objetivos do intervalo.
- **Quadros.** A preparação grava ouro, XP, farm (tropas + selva) e nível. Documentos anteriores, só com ouro, são regravados na próxima preparação quando o bruto local existe.
- **Selo do Histórico.** `GET /api/v1/matches` traz `reviewPurchases {ready, total}` da conta local, a partir da extração mais recente das versões correntes, ou null.

## CC-901 — Sincronização do histórico com a Riot

Ao abrir a aba Histórico, a interface pede `POST /api/v1/history/sync` uma vez por visita e mostra o estado num aviso discreto acima dos filtros; o botão **Atualizar** repete o pedido quando o intervalo mínimo termina. A importação manual pelo ID continua igual.

- **Escopo:** só a conta própria de `RIFTLAB_RANKED_ACCOUNT_FILE`; a rota não recebe conta, PUUID nem Riot ID. Sem `RIFTLAB_RIOT_API_KEY` ou sem conta configurada, responde `UNAVAILABLE` (`RIOT_KEY_MISSING`/`ACCOUNT_NOT_CONFIGURED`) e não grava nada.
- **Execução:** a requisição só cria uma linha `history_sync_runs` QUEUED (uma ativa por conta, índice único parcial). O worker resolve a conta (Account-V1) e lista até `history_sync_max_ids` (20) IDs `type=ranked` (Match-V5) desde o início da última sincronização bem-sucedida menos 1 h, ou dos últimos `history_sync_lookback_days` (14) na primeira vez. Partidas já salvas ou com importação RIOT não falha contam como conhecidas; partidas expurgadas não voltam (CC-603); as demais viram IMPORT RIOT pelo caminho normal do worker, com limitador e cooldown.
- **Intervalo mínimo:** 300 s entre execuções; 3600 s depois de `RIOT_AUTH_FAILED` ocorrido no processo atual (renovar a chave exige reiniciar, o que libera o pedido); `Retry-After` da Riot quando maior. Execução RUNNING por mais de 10 min vira `FAILED SYNC_STALE` no próximo pedido; troca da conta configurada encerra a execução pendente com `ACCOUNT_CHANGED` sem rede.
- **Estado (`HistorySyncStatus`):** `state` IDLE/QUEUED/RUNNING/SUCCEEDED/FAILED/UNAVAILABLE; `reason` com o código do erro da última execução ou `COOLDOWN` depois de uma bem-sucedida ainda no intervalo; `requestedAt`, `finishedAt`, `nextAllowedAt`; contagens `listed`, `newEnqueued`, `alreadyKnown`, `skippedPurged`. A execução guarda só contagens e códigos: nenhum PUUID, Riot ID ou ID de partida.
- **Interface:** enquanto a execução está ativa, consulta `GET` a cada 2 s; com partidas novas, recarrega a primeira página da lista a cada 5 s (até 12 vezes) sem voltar ao estado de carregamento, para os cartões aparecerem conforme as importações terminam.

## Painel descritivo (CC-309)

`GET /api/v1/dashboard?origin=REAL|DEMO&window=20[&queue=RANKED_SOLO_5x5|RANKED_FLEX_SR]` é
somente leitura no banco, sem coleta ou inferência. `window` aceita 1–100. Sem `queue`, a fila
do recorte é a da partida própria mais recente (`queueSelection` AUTO; empate fica com
Solo/Duo); com `queue`, é a pedida (EXPLICIT). `queue` na resposta traz 420 ou 440 e `queues`
o total de partidas próprias por fila (CC-904); valor fora dos dois retorna 422.
REAL requer a conta local e o vínculo HMAC da observação mais recente dessa conta
em League-V4; sem esse vínculo, retorna `IDENTITY_UNAVAILABLE`, sem atribuir dados
de outros participantes. Partidas com vínculo ambíguo são excluídas. DEMO é uma
seleção explícita do participante sintético 1 e nunca representa desempenho real.
As origens não são misturadas. Ausência de chave Riot não troca REAL por DEMO.

A janela contém até N partidas concluídas importadas, ordenadas por término
(`start_at + duration_seconds`) decrescente, com desempate por ID decrescente.
`totalMatches` é a quantidade da conta nessa origem/fila; `sampledMatches` é a
janela efetiva. `winSamples` conta apenas resultados conhecidos e `winRate` é
`wins / winSamples`. `kdaSamples` conta somente placares completos e `kda` é
`(Σ kills + Σ assists) / max(1, Σ deaths)` nessa subamostra. Amostra sem o dado
retorna `null`, nunca zero inventado. Denominadores de resultado e KDA podem ser
menores que a janela e são mostrados separadamente na interface.

`champions` traz até três campeões mais frequentes na janela e suas contagens;
`distinctChampions` conta todos os distintos. `recentMatches` traz até cinco
partidas da janela com o participante correto, fatos nullable e catálogo do patch
fixado, sem identificador pessoal. Tropas exibidas somam tropas de rota e monstros
neutros apenas quando ambos os campos são conhecidos. O link ao Histórico abre
a lista geral, explicitamente indicada como tal.

`progress` é atividade da instalação local, de todas as datas, filtrada pela origem,
incluindo estudo de terceiros e exercícios sem pistas; não é desempenho da conta
nem estimativa de aprendizagem. `attempts` conta tentativas iniciadas, `revealed`
tentativas reveladas, `reflections` tentativas com reflexão e `matches` partidas
distintas com tentativa. Repetição idempotente não cria nova contagem. Todos os
agregados e a lista usam a mesma transação PostgreSQL READ ONLY/REPEATABLE READ.

## Experimento de planejamento tardio (CC-701, late_planning_v1)

Extractor offline separado do escopo servido de decisões. Consome estados v9,
catálogo/regras fixados e recorte explícito. Ordens 3–5 são MAIN possuídos +1.
Mantém todos os motivos de inelegibilidade, exceto o limite de ordem da tarefa
antiga. Primeira compra após a conclusão principal anterior fixa a âncora; exige
índice e timestamp anteriores ao alvo, inventário certo e ordinal coerente. Não
avança para uma âncora posterior se a primeira falhar. Candidatos vêm do mesmo
enumerador puro do produto; alvo ilegal nunca é inserido artificialmente.

Features/máscaras são reconstruídas na âncora. Ouro é removido inteiramente;
vetores late_context_v1 usam inventário próprio, tempo/nível e composição pública,
além de atributos estáticos dos candidatos. Sem itens inimigos sob névoa ou fatos
finais. Composição desconhecida e nível ausente têm máscaras, não zeros fictícios.
Baseline condicionado à build usa apenas treino e suavização de peso10 sobre a
popularidade campeão/ordem. Suporte exige60 linhas legais e10 partidas distintas
na célula de treino. Avaliação e artefatos experimentais não alteram rotas, scope,
modelos ativos ou o tratamento atual do 1º/2º item. Ganho de imitação não demonstra
pertinência estratégica; essa finalidade exige revisão humana específica.
