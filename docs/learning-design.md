# Aprendizagem estratégica e autonomia

## Análise direta e prática opcional

A tela de decisão abre por padrão em Analisar partida, com comparação direta e objetivo equilibrado, sem criar tentativa de aprendizagem, pedir escolha ou contabilizar resposta/reflexão. Praticar decisão é explícito, inclusive por `?mode=practice`, e mantém a sessão pedagógica abaixo. A comparação usa o critério realmente servido, não garantia de adequação estratégica. Ver análise antes de praticar contamina uma avaliação independente daquela decisão; a interface recomenda outra decisão e os registros de prática não constituem, por si, evidência de aprendizagem. Trocar de modo reinicia a visualização, mas não apaga registros persistidos. Resultados e gates de pesquisa permanecem inalterados.

## Público e objetivo

Iniciantes e jogadores de elos mais baixos que querem compreender decisões de itemização. O sistema ensina leitura de contexto e comparação de alternativas; não mede valor pessoal, não cria classificação de habilidade alternativa e não promete subir de elo. O usuário sem partidas próprias começa com cenários didáticos sintéticos e glossário.

## Sessão de estudo

1. Abrir uma partida já encerrada ou cenário didático identificado.
2. Mostrar somente informações pré-decisão, sem resultado final, compra histórica, score ou ordem do modelo.
3. Perguntar: qual necessidade você priorizaria? Mostrar opções de objetivo e glossário.
4. Apresentar até três alternativas plausíveis em ordem neutra; pedir escolha e justificativa curta opcional. Incluir “não sei”. A ordem neutra é estável por tentativa e não expõe o ranking por outro indicador.
5. Oferecer pistas em dois níveis: observar ameaças e conferir custo/atributos. Pistas se baseiam em fatos disponíveis, não revelam um vencedor.
6. Após resposta ou pedido explícito para revelar, comparar alternativas e compromissos. Não tratar a compra histórica nem o top-1 do modelo como gabarito universal. Onde houver rubrica de especialista, identificar essa origem; onde não houver, falar em comparação, sem nota automática de acerto.
7. Registrar reflexão: o que mudaria se a ameaça ou o orçamento fosse diferente?
8. Apresentar cenário independente do conjunto didático para tentativa sem dicas e acompanhar evolução local.

## Fronteira de integração

Somente Match-V5 para partidas concluídas e cenários didáticos curados. Emenda de 28/09/2026 (decisão do usuário, conceito C): League-V4 é permitido **somente em leitura e somente para a conta do próprio usuário**, para exibir o elo oficial em Minha posição; não há consulta de elo de terceiros, escada, pódio nem comparação entre jogadores, e o elo não é usado como feature de modelo nem como avaliação do aluno. Nenhum uso de Live Client Data, Spectator para estado corrente, leitura de memória, interceptação de tráfego, captura de tela da partida ativa, overlay ou automação de teclado/mouse/compras. O programa pode estar aberto enquanto alguém joga, mas não oferece acompanhamento da partida em andamento. Não há formulário de estado de uma partida ao vivo: edição de orçamento é exclusivamente cenário derivado de uma partida encerrada/didática e recebe essa identificação.

Pós-partida não autoriza apresentar conhecimento futuro como se o jogador o tivesse. Features usam corte temporal; fatos sobre adversários sem garantia de observabilidade naquele momento recebem aviso e não geram crítica ao jogador. MVP limita justificativas a composição inicial, estado próprio conhecido e informações comprovadamente acessíveis; inventário inimigo detalhado não deve virar cobrança de que o jogador soubesse algo sob névoa de guerra.

## Papel dos modelos

O ranker estima plausibilidade contextual das alternativas para comparação. O agrupamento, se validado, ajuda a estudar famílias de situações, não a classificar jogadores. O interpretador ajustado converte o objetivo declarado em enumeração; não avalia livremente a inteligência do aluno nem corrige justificativa aberta sem rubrica. Pistas e glossário são curados. Modelos não são a fonte de verdade sobre “o que você deveria ter feito”.

## Persistência e interface

learning_attempts: UUID, decision_id, objective, candidate_ids em ordem apresentada, selected_item_id nullable, skipped boolean, rationale até 1000 caracteres opcional, hint_level 0..2, revealed_at nullable, analysis_id nullable, created_at e exercise_version. Antes de reveal, API não inclui ranking/scores/compra histórica; depois inclui comparação e source dos critérios. Tentativa é local, não um perfil público. Dados de aprendizagem não alimentam treino automaticamente.

UI: modo Estudo como padrão; revisão direta pode ser revelada explicitamente, sem impedir acessibilidade ou impor resposta obrigatória. “Não sei” oferece pista ou comparação sem penalização. Glossário explica termos como composição, componente e custo restante. Relatório separa resposta inicial, pistas consultadas, comparação e reflexão.

## Avaliação pedagógica

O glossário da jornada real usa texto curado e independente da situação, disponível antes da escolha, durante o estudo e após a revelação. Abrir ou fechar o glossário não altera a tentativa, a ordem de candidatos, o nível de pistas ou a visibilidade da compra histórica. A seleção do modo de transferência sem ajuda oculta o glossário, inclusive antes de iniciar a tentativa. Essa integração é funcionalidade de apoio; testes técnicos não demonstram aprendizagem.

Além das métricas de ranking, avaliar transferência: pré-teste sem ajuda, sessão de estudo e pós-teste em casos diferentes equivalentes, com rubrica independente revisada. Medir justificativa por critérios observáveis, escolha plausível, uso de pistas e autoconfiança declarada. Amostra piloto desejada: 6–10 voluntários adultos, participação opcional e dados pseudônimos; verificar exigências da instituição antes do estudo. Não presumir aprovação institucional. Sem participantes, realizar teste de usabilidade técnico e relatar estudo de aprendizagem como pendente.

Controlar familiaridade com campeão e evitar repetir os mesmos casos. Se houver capacidade, contrabalançar versões A/B dos testes e comparar revisão direta com modo de estudo. Piloto pequeno não demonstra efeito causal nem generalização; tempo de uso e satisfação sozinhos não provam aprendizagem. Não avaliar melhora apenas pelo quanto o jogador imita o modelo.

## Políticas

O desenho prioriza ferramentas de treinamento e reflexão sobre partidas concluídas, preservando a decisão do jogador. Isso é compatível com a direção das políticas consultadas, mas não constitui aprovação formal da aplicação pela Riot. Antes de disponibilizar a jogadores, registrar o produto e revalidar caso de uso, avisos e condições de dados. Não lançar recomendações em tempo real como extensão automática.

Fontes oficiais consultadas em 14/09/2026: [documentação e política de LoL](https://developer.riotgames.com/docs/lol), [políticas gerais](https://developer.riotgames.com/policies/general).
