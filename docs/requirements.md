# Requisitos e jornadas

Status: especificação com rastreabilidade para o código entregue (13/10/2026). A matriz abaixo diz o que está entregue, parcial, pendente ou só no protótipo.

## Atores e limites

Operador local: importa partidas, acompanha jobs e executa avaliação. Jogador/revisor: consulta uma partida disponibilizada localmente, define objetivo e registra julgamento. Treinamento é comando administrativo, nunca acionado por usuário público. MVP de estação individual, vinculado a loopback, sem cadastro ou autenticação de contas. Exposição pública/multiusuário exige novo escopo com autenticação, autorização, TLS e isolamento; não publicar a API local diretamente.

## Requisitos funcionais

| ID | Comportamento | Entrega |
| --- | --- | --- |
| RF01 | Abrir demonstração sem chave nem conexão externa | S1, S6 |
| RF02 | Importar match ID BR1 válido sem duplicar partida/job ativo | S2 |
| RF03 | Exibir progresso, falhas e tentativa novamente | S2 |
| RF04 | Selecionar participante e pontos de compra suportados | S2 |
| RF05 | Mostrar estado, proveniência e ausência de dados | S2 |
| RF06 | Gerar candidatos legais do catálogo daquele patch | S3 |
| RF07 | Comparar até três itens e custo restante, sem obrigação de três | S3 |
| RF08 | Ordenar com modelo versionado e sinalizar insuficiência | S4 |
| RF09 | Definir objetivo por formulário ou texto validado | S5 |
| RF10 | Registrar julgamento e exportar relatório JSON/HTML | S5 |
| RF11 | Executar experimentos reproduzíveis e consultar manifesto | S4, S6 |
| RF12 | Expurgar dados locais por partida e derivados associados | S6 |

## Casos de uso

CC-102 prototipa UC01–06 em baixa/média fidelidade, com dados ilustrativos e sem API real; ver [revisão de UX](ux-review.md) para a matriz de fidelidade por caso de uso, responsividade e acessibilidade.

### UC01 — Importar partida (RF01–03)

Pré-condição: aplicação local saudável; modo demo ou chave configurada no servidor. Operador informa match ID. API valida padrão/rota, cria job durável e devolve 202. Worker obtém partida e timeline, valida e persiste antes de marcar sucesso. UI acompanha por polling. Reenvio de mesma identidade reaproveita job ativo. 429 aguarda Retry-After; 403 apresenta credencial/acesso inválido sem expor segredo; 404 distingue partida inexistente. Falha parcial não libera partida como analisável. Pós-condição: partida consistente ou falha rastreável.

### UC02 — Revisar compra (RF04–08)

Pré-condição: importação concluída. Selecionar participante e decisão. Ver estado imediatamente anterior à compra, granularidade temporal e dados ausentes. Solicitar alternativas e comparar custo restante, suporte e evidências. Fora do recorte retorna motivo; falta de catálogo ou inventário inconsistente impede ranking. Orçamento desconhecido permite planejamento de alvo, nunca promessa de compra imediata. Pós-condição: análise persistida com versões e entrada imutável.

### UC03 — Informar objetivo (RF09)

Pré-condição: decisão analisável. Escolher objetivo estruturado ou escrever até 500 caracteres. Texto é convertido em enumeração validada; usuário confirma a interpretação. Ambiguidade mostra formulário. Objetivo altera somente regras explícitas de desempate/reordenação documentadas, sem inventar atributos do jogo. Falha do modelo não altera objetivo anterior. Pós-condição: nova análise vinculada ao objetivo confirmado.

### UC04 — Avaliar e exportar (RF10)

Revisor avalia alternativas em ordem aleatorizada, sem identidade do modelo na coleta experimental. Pode abster-se. Julgamento tem versão da rubrica e identificação pseudônima do revisor. Exportação omite credenciais, PUUID e Riot ID; HTML escapa texto e não carrega scripts externos. Feedback não dispara retreinamento automático. Pós-condição: avaliação auditável e relatório portável.

### UC05 — Operar e remover dados (RF11–12)

Operador consulta modelo ativo e falhas; executa treinamento via CLI com manifesto. Exclusão requer confirmação explícita na interface e informa derivados afetados. Remover partida, estados, análises, feedback vinculado e arquivos brutos; invalidar datasets/modelos que dependam dela até reavaliação. Não alegar que remover linhas desfaz aprendizagem de um modelo já treinado.

## Requisitos não funcionais

RNF01: execução e testes fundamentais em CPU, sem chamada paga na CI. RNF02: p95 de ranking aquecido ≤1 s em 100 consultas locais com até 100 candidatos, excluindo importação/LLM; medir e registrar CPU/RAM. RNF03: interpretação de texto tem timeout configurável de 15 s e alternativa imediata por formulário. RNF04: idempotência transacional e recuperação após término do worker. RNF05: toda análise registra patch, catálogo, schema, modelo, candidato e origem de orçamento. RNF06: teclado, foco visível, texto para estados e contraste WCAG AA como objetivo de UI. RNF07: nenhum segredo ou identificador do jogador em logs/exportações/fixtures públicas. RNF08: lockfiles e seeds, ambiente de teste isolado, migrações verificadas em PostgreSQL vazio. RNF09: interface portuguesa responsiva de 360 a 1440 px, verificada em 360, 390, 1280 e 1440 px. RNF10: conformidade com APIs e políticas da Riot (Match-V5; League-V4 só leitura e só da conta própria; Data Dragon e Ranked Emblems com crédito e aviso de não endosso; limitador global e Retry-After). RNF11: consistência visual do conceito C (tokens únicos; roxo só ação/seleção, verde/vermelho só resultado, cor do elo só classificação; sem gradiente ou brilho decorativo; contraste AA em todos os elos). RNF12: transparência de métricas (denominador e origem REAL/DEMO sempre visíveis; ausência nunca vira zero; nenhuma afirmação causal). Metas são critérios de validação, não medições já obtidas.

## Aprendizagem ativa — requisitos adicionais

RF13: modo Estudo oculta ranking/compra histórica até escolha, “não sei” ou revelação explícita. RF14: pistas graduais e glossário curado para iniciantes. RF15: persistir tentativa, objetivo, escolha e pistas, distinguindo comparação de gabarito. RF16: cenário de transferência independente sem ajuda e exportação da reflexão. RF17: recusar fonte de partida em andamento; não integrar cliente ao vivo.

UC06 — Estudar decisão: pré-condição partida concluída/cenário didático. Aluno observa contexto, declara prioridade e escolhe em alternativas de ordem neutra; pode usar duas pistas ou “não sei”. Só após reveal recebe comparação. Cenário inválido mostra limitação, não nota de erro. Pós-condição: tentativa preservada e reflexão opcional. RF13–14 começam na S3; persistência/exportação RF15–16 na S5 e avaliação na S6; RF17 desde S1. Ver learning-design.md, que complementa e prevalece sobre qualquer fluxo antigo de revelação automática.

## Interface do conceito C — requisitos adicionais (28/09/2026)

Decisão do usuário: navegação Painel, Campeões, Minha posição e Histórico; estudo acessado pelo Histórico; Minha posição só com o elo da conta própria. Cards CC-308, CC-309 e CC-310 (propostos) e adaptações em CC-208/307.

| ID | Comportamento | Entrega |
| --- | --- | --- |
| RF18 | Navegar por Painel, Campeões, Minha posição e Histórico em desktop e mobile, com aviso de demonstração quando os dados forem sintéticos | S3 (CC-308) |
| RF19 | Painel com taxa de vitória, KDA médio, decisões revistas, campeões no recorte e em foco, sempre com denominador | S3 (CC-309) |
| RF20 | 5 partidas mais recentes no Painel com acesso ao Histórico completo; Histórico com busca, filtro por resultado, resumo do recorte e acesso ao contexto da partida | S3 (CC-308, CC-309) |
| RF21 | Guia por campeão com nome e retrato oficiais do patch, habilidades do Data Dragon, trajeto de itens observado com amostra, cobertura e abstenção, e perguntas de estudo | S2–S3 (CC-208, CC-307) |
| RF22 | Minha posição: elo, divisão, PDL 0–100, próximo degrau e variação entre instantâneos da conta própria; estados de não classificado e indisponível; sem ranking de terceiros | S3 (CC-310) |

### UC07 — Consultar painel e histórico (RF18–RF20)

Pré-condição: partidas concluídas importadas ou demonstração aberta. O jogador vê taxa de vitória e KDA do recorte com o tamanho da amostra, as 5 partidas mais recentes e os campeões em foco; abre o Histórico, busca, filtra e escolhe “Contexto”, que leva ao UC02/UC06. Recorte vazio mostra orientação. Pós-condição: nenhuma alteração de dados.

### UC08 — Estudar campeão (RF21)

O jogador escolhe um campeão; vê retrato e nome do patch fixado (ou texto de reserva), habilidades, trajeto de itens observado com denominador e perguntas de matchup. Sem suporte suficiente, o sistema se abstém e diz por quê. Nunca apresenta “build ideal”.

### UC09 — Consultar minha posição (RF22)

Pré-condição: conta própria configurada e chave válida, ou demonstração. O sistema consulta League-V4 somente para essa conta, grava um instantâneo local e mostra elo, divisão, PDL e próximo degrau; variação só é associada a uma partida quando não houver ambiguidade. Falha ou conta não classificada mostram estado claro, sem valor inventado. Não há consulta de outras contas.



## Rastreabilidade: requisito → caso de uso → módulos → testes → estado (13/10/2026)

**Legenda:**
- **Entregue:** funciona no código, com teste automatizado.
- **Parcial:** parte do comportamento existe; a lacuna está descrita.
- **Pendente:** não implementado.
- **Protótipo:** só no protótipo ilustrativo do CC-102, sem API real.

Os resultados medidos estão em [results.md](results.md).

| RF | UC | Módulos | Testes representativos | Estado |
| --- | --- | --- | --- | --- |
| RF01 demonstração sem chave nem rede | UC01 | `ingestion/infrastructure/demo_source`, `catalog/infrastructure/demo_source` | `e2e/real-stack`, `e2e/study` (sem host externo), guarda de rede dos testes | Entregue |
| RF02 importar BR1 sem duplicar | UC01 | `ingestion/application`, `api/routes/imports` | `test_import_routes`, `test_import_idempotency_race`, `test_end_to_end_pipeline` | Entregue |
| RF03 progresso, falha e nova tentativa | UC01 | `ImportProgressPage`, `preparation/` | `e2e/recovery`, `test_preparation`, `test_worker_processing` | Entregue |
| RF04 participante e pontos de compra | UC02 | `decisions/`, `MatchPage`, `DecisionList` | `test_decision_routes`, `e2e/keyboard`, `e2e/mobile-360` | Entregue |
| RF05 estado anterior, proveniência e ausência | UC02 | `timeline/`, `decisions/`, `DecisionDetailPage` | `test_decision_extraction`, `test_timeline_reconstruction` | Entregue |
| RF06 candidatos legais do catálogo do patch | UC02 | `candidates/`, `catalog/` | `unit/candidates`, `test_analyses` | Entregue |
| RF07 comparar até três com custo restante | UC02 | `analyses/`, `StudyPanel` | `test_analyses`, `StudyPanel.test` | Entregue |
| RF08 ordenar com versão e sinalizar insuficiência | UC02 | `ranking/`, `ml/support`, `ml/registry` | `test_ml_registry`, `unit/test_ml_support*`, `test_end_to_end_pipeline` | Entregue: baseline versionado com suporte e abstenção. Nenhum modelo promovido, por evidência; `registryFallback` com motivo |
| RF09 objetivo por formulário ou texto validado | UC03 | `objectives/`, `ObjectivePicker` | `test_objective_*`, `test_objective_gate`, `e2e/study` | Entregue: texto por regras (o modelo ajustado foi reprovado no gate) |
| RF10 julgamento e exportação JSON/HTML | UC04 | `reviews/`, `AfterComparison` | `test_reviews_reports`, `e2e/real-stack` (download e abertura) | Entregue |
| RF11 experimentos reproduzíveis e manifesto | UC05 | `ml/experiment`, `ml/registry`, `dataset/`, `cc ml *` | `test_dataset_corpus`, `test_ml_registry`, `test_expert_review` | Entregue |
| RF12 expurgo com confirmação e derivados | UC05 | `ingestion/purge`, `PurgePanel`, `cc retention` | `test_purge`, `test_raw_store_purge`, `e2e/purge`, `e2e/real-stack` | Entregue (CC-603) |
| RF13 estudo oculta ranking até revelar | UC06 | `learning/`, `StudyPanel` | `test_learning_attempts`, `StudyPanel.test` | Entregue |
| RF14 pistas graduais e glossário | UC06 | `learning/` (pistas), `GlossaryDisclosure` no `StudyPanel` | `StudyPanel.test`, `e2e/study`, `e2e/glossary` | Implementado: duas pistas e glossário curado na jornada real, ocultos na prática de transferência sem ajuda |
| RF15 persistir tentativa, objetivo, escolha e pistas | UC06 | `learning/models` | `test_learning_attempts` | Entregue |
| RF16 transferência sem pistas e reflexão exportada | UC06 | `learning/`, `reviews/report` | `test_reviews_reports`, `e2e/study` (jornada com transferência) | Entregue |
| RF17 recusar partida em andamento | UC01 | `ingestion/infrastructure/riot_source` (`GameComplete`) | `test_worker_processing`, `unit` do validador | Entregue |
| RF18 quatro áreas em desktop e mobile | UC07 | `app/MainNav`, `AreaPages` | `e2e/concept-c`, `e2e/concept-c-failures` | Entregue |
| RF19 Painel com taxa de vitória, KDA e decisões revistas | UC07 | — | — | Pendente (CC-309 proposto): o Painel mostra só partidas recentes e o card de elo |
| RF20 cinco recentes; Histórico com busca e filtro | UC07 | `features/history` | `e2e/cc311`, `HistoryPage.test` | Parcial: busca por id/campeão e filtro de origem; filtro por resultado e K/D/A indisponíveis (a API não identifica a conta própria na partida) |
| RF21 guia por campeão | UC08 | `champions/` (nome e retrato no Contexto) | `e2e/champions` | Parcial: nomes e retratos oficiais no Contexto (CC-208); guia de estudo (CC-307) pendente |
| RF22 Minha posição da conta própria | UC09 | `ranked/`, `features/ranked` | `test_ranked_persistence`, `e2e/ranked`, `e2e/concept-c-failures` | Entregue |

**RNF:**
- RNF01 CPU e sem rede paga: entregue (guarda de rede, CI sem segredos).
- RNF02 p95 ≤ 1 s: entregue (ranking p95 2,5 ms; legalidade p95 163 ms).
- RNF03 timeout de 15 s com formulário: entregue (`e2e/study`, timeout real de 16 s no cliente).
- RNF04 idempotência e recuperação: entregue (leases, retomada, testes de concorrência).
- RNF05 versões na análise: entregue (inclui `registryVersion` e `registryFallback`).
- RNF06 acessibilidade: entregue para teclado, foco visível e alertas. O contraste AA é medido nos elos; não houve auditoria AA completa.
- RNF07 sem identificadores em logs e exportações: entregue (auditoria do CC-603, relatório sem ids, lápide com HMAC).
- RNF08 lockfiles, seeds, testes isolados e migrações em banco vazio: entregue.
- RNF09 360–1440 px: entregue (specs em 360/390/1280/1440).
- RNF10 políticas da Riot: entregue no código (limitador, Retry-After, League-V4 só da conta própria, créditos). O registro do produto no portal da Riot não foi feito, e o projeto não é publicado a jogadores.
- RNF11 consistência visual do conceito C: entregue nas áreas existentes.
- RNF12 transparência de métricas: entregue onde há métricas (denominadores, DEMO/RIOT); as métricas do Painel (CC-309) estão pendentes.
