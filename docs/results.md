# Relatório de resultados

Estado em 15/10/2026. Todos os números saem de manifestos e relatórios gravados uma única vez
(`.private/artifacts/*/run.json` e `test.json`, `.private/validation/sprint-*/`). Antes de
escrever, conferi cada valor desta página contra o arquivo de origem. A seção da revisão por
especialistas fica **pendente** até as notas dos revisores humanos (CC-602).

## 1. Pergunta e contribuição

**Pergunta.** Dado o estado de uma partida concluída imediatamente antes de uma compra, um
sistema local consegue:

1. reconstruir esse estado com honestidade sobre a incerteza;
2. propor alternativas de compra legais e ordenadas;
3. apoiar o estudo de um iniciante sem tratar a compra histórica como gabarito?

**Contribuição.** É de engenharia e de método, mais do que de desempenho de modelo:

- Reconstrução temporal pré-compra com proveniência, em que incerteza nunca vira valor
  inventado, e regras empíricas liberadas só por confirmação pré-registrada.
- Protocolo de comparação com teste reservado executado uma vez, intervalos por bootstrap de
  partida e resultados negativos preservados.
- Separação explícita entre **imitação** (prever o que o jogador comprou) e **pertinência**
  (o que um especialista consideraria adequado). A segunda depende de revisão humana.
- Jornada de estudo que oculta o ranking até a revelação explícita, com objetivo declarado,
  pistas graduais, reflexão e exportação auditável.

## 2. Dados

| Etapa | Resultado | Fonte |
| --- | --- | --- |
| Auditoria de acesso (CC-105) | 122 partidas reais de 5 contas com consentimento; recorte BOTTOM com Caitlyn, Ezreal e Kai'Sa | `validation/sprint-1/CC-105` |
| Coleta incremental (CC-206) | 50 partidas (16.16/16.17), 117 requisições, nenhum 401/403/429 | `validation/sprint-2/CC-206` |
| Regras de inventário 16.16/16.17 (CC-207) | **Negativo**: nenhuma regra liberada; 0 de 1.461 pontos elegíveis | `validation/sprint-2/CC-207` |
| Confirmação 16.19 (CC-322) | 6 regras aprovadas na confirmação única; pontos elegíveis no recorte de 0 para 24 de 227 | `validation/sprint-3/CC-322` |
| Corpus 16.19 ampliado (CC-323/324) | 1.173 partidas; após 5 regras novas, 676 pontos elegíveis de 2.191 no recorte | `validation/sprint-3/CC-323`, `CC-324` |
| Dataset congelado (CC-401) | 1.127 partidas por corte cronológico 788/169/170; 469/118/89 decisões (treino/validação/teste) | `dataset/dataset-16.19-v2.json` (`430e4c69…`) |
| Recorte ampliado (CC-702, experimento) | Mesmo corpus e divisão com 10 atiradores (≥ 60 partidas no treino); 1.685 elegíveis de 5.310 pontos; 1.176/284/225 decisões; linhas dos 3 originais idênticas às anteriores | `dataset/dataset-16.19-v3-*-slices.json`, `validation/sprint-7/CC-702` |
| Coleta em escala (CC-703) | 10.000 partidas novas do 16.19 (bola de neve espalhada, 5 sorteadas por jogador); corpus v3 com 10.662 partidas; 16.204 decisões elegíveis (11.418/2.391/2.395); últimos 3 dias do patch com 22% das partidas, contra 77% antes | `validation/sprint-7/CC-703` |

**Contaminação por jogador:** 72 de 118 decisões de validação e 46 de 89 de teste são de
jogadores que também aparecem no treino (coleta em bola de neve). Por isso todo resultado é
reportado também no subconjunto sem jogador em comum.

## 3. Ranking (imitação da compra histórica)

Protocolo `configs/experiments/comparison-protocol-v1.yaml`:
- métrica principal MRR, com hit@k;
- IC 95% por bootstrap de partidas (2.000);
- seleção só na validação e teste uma vez.

Os modelos são:
- **B0:** popularidade por campeão e ordem do item;
- **B1:** regras explícitas de aproveitar componentes e custo restante;
- **M1:** CatBoostRanker PairLogit em CPU, com até 8 configurações por variante.

### 3.1 No instante da compra (CC-402)

| Modelo | Validação MRR | Teste MRR [IC 95%] | Teste sem jogador do treino |
| --- | ---: | ---: | ---: |
| B0 | 0,797 | 0,766 [0,685–0,843] | 0,692 |
| B1 | 0,855 | 0,793 [0,724–0,859] | 0,742 |
| M1 completo | 0,978 | 0,975 [0,944–1,000] | 0,961 |
| M1 sem economia | 0,832 | 0,788 [0,714–0,857] | 0,726 |

**Leitura.** O ganho vem do ouro do instante da compra. Só cerca de 4 dos ~106 itens legais
cabem no ouro estimado, e o modelo aprende "o item quase pagável". Isso é imitação do
momento, não pertinência.

### 3.2 Planejamento do próximo item, sem o atalho do ouro (CC-407/408)

| Modelo | Validação MRR | Teste MRR [IC 95%] | Teste sem jogador do treino |
| --- | ---: | ---: | ---: |
| B0 popularidade | 0,795 | 0,760 [0,681–0,838] | 0,674 |
| B1 heurística | 0,049 | 0,042 [0,027–0,059] | 0,047 |
| M1 sem contexto rico | 0,806 | 0,749 [0,664–0,832] | 0,668 |
| M1 com contexto rico (CC-408) | 0,806 | 0,761 [0,673–0,844] | 0,663 |

**Resultado negativo.** Nesta amostra, composição, custo restante e contexto rico não melhoram
a previsão além da popularidade por campeão e ordem do item. Adversário de rota, itens
inimigos e diferenças de ouro e abates também não. O teste de planejamento foi consultado
duas vezes: no CC-407 e depois no CC-408, o que está declarado.

### 3.3 Clusters de contexto (CC-403)

| k | Silhueta | Estabilidade (Rand, 5 sementes) | Validação MRR |
| --- | ---: | ---: | ---: |
| 3 | 0,077 | 0,76 | 0,810 |
| 5 | 0,075 | 0,85 | 0,805 |
| 8 | 0,090 | 0,87 | 0,803 |

Sem cluster, o MRR de validação é 0,806. O ganho máximo foi 0,004, abaixo do limiar
pré-fixado de 0,01, e a silhueta perto de zero indica que não há grupos naturais. **Clusters
descartados.**

### 3.4 Suporte e abstenção (CC-404)

- **Barreira de validade** antes do ranking: estado inválido, sem candidato legal, patch sem
  regras, campeão fora do recorte.
- **Suporte:** contagem de decisões de treino por célula (campeão × ordem). Não é
  probabilidade.
- **Escolha do limiar:** regra fixada antes; ganha o τ de maior cobertura com risco
  (1 − hit@3) ≤ 0,15 na validação.
- **Resultado:** as 6 células têm ≥ 44 decisões, então nenhum limiar abstém e τ = 0.
  Validação: cobertura 1,0 e risco 0,119. Teste: cobertura 1,0 e risco 0,112.
- **Com dez atiradores (CC-704):** as 20 células têm ≥ 26 decisões e τ continua 0.
  Validação: cobertura 1,0 e risco 0,096 (280 decisões). Teste: cobertura 1,0 e risco 0,080
  (224).
- **Quando há abstenção:** no recorte atual, ela vem da barreira de validade, de campeão fora
  do recorte ou de patch sem suporte.

### 3.5 Critério servido

O produto serve **B0 (popularidade)** atrás da barreira e do suporte. Desde o CC-704, o
artefato servido é `popularity-16.19-v2.json`: 20 células, uma por campeão e ordem do item,
para os dez atiradores do recorte v2. As seis células dos campeões originais são iguais às do
v1. Sem referência para a
célula, ele cai nas regras explícitas. Nenhum modelo superou o baseline na tarefa de
planejamento, então nenhum foi promovido.

O registro (CC-405) mantém versões com hash. Uma análise grava `registryVersion`, e grava
`registryFallback` quando não há versão utilizável: sem versão ativa, arquivo alterado ou
dataset invalidado por expurgo (CC-601/603).

### 3.6 Recorte ampliado para dez atiradores (CC-702, experimento)

O mesmo corpus, a mesma divisão e o mesmo protocolo foram usados com um recorte experimental,
`configs/experiments/scope-16.19-marksmen10.yaml`. A regra, fixada antes da medição, foi
atirador com pelo menos 60 partidas na rota inferior do treino: Caitlyn, Kai'Sa, Jinx, Ezreal,
Jhin, Yunara, Tristana, Ashe, Miss Fortune e Twitch. O pré-registro foi selado antes da
remedição.

- **Dados:** a elegibilidade fica entre 29% e 36% em todos os campeões, e cada um tem pelo
  menos 26 decisões de 2º item no treino. As linhas dos três campeões originais saíram
  idênticas às do dataset v2.
- **Fatias:** as métricas saem por campeões originais, campeões novos e campeão. O teste dos
  campeões novos nunca tinha sido visto; o dos originais já foi consultado três vezes.

| Planejamento sem ouro | Validação | Teste (225) [IC 95%] | Campeões novos (136) | Sem jogador do treino (140) |
| --- | ---: | ---: | ---: | ---: |
| B0 popularidade | 0,764 | 0,753 [0,707–0,798] | 0,748 | 0,715 |
| M1 completo | 0,742 | 0,743 [0,699–0,786] | 0,746 | 0,711 |
| M1 com contexto rico | 0,742 | 0,735 [0,691–0,776] | 0,729 | 0,694 |

**Resultado negativo confirmado.** Com 2,5 vezes mais decisões, o modelo continua empatado com
a popularidade, também na fatia nova. O intervalo do B0 no teste ficou com cerca de 0,09 de
largura, contra 0,16 antes. No instante da compra, o padrão do CC-402 se repete: B0 0,763 e M1
0,944, que cai a 0,804 sem as features econômicas.

Entre variantes do M1, diferenças da ordem de 0,015 são ruído de treino: duas variantes que só
diferem em colunas constantes deram 0,743 e 0,729. Por campeão, as fatias têm de 14 a 44
decisões de teste e são só descritivas.

O recorte foi levado ao produto no CC-704 (§3.4 e §3.5).

## 4. Desempenho e custo (CC-406)

Ambiente: Intel Core i3-7020U (2 núcleos/4 threads), 15,9 GB de RAM, sem GPU utilizável,
Python 3.12, 1 thread. São 100 consultas aquecidas, com 106 candidatos legais cada.

| Etapa | p50 | p95 |
| --- | ---: | ---: |
| Enumeração de candidatos legais | 65 ms | 163 ms |
| Ranking B0 + suporte | 1,1 ms | 2,5 ms |
| Pontuação do M1 | 8,7 ms | 18 ms |

A meta RNF02 (p95 ≤ 1 s) foi atendida; o custo dominante é a legalidade, não o ranking.

| Treino | Tempo | Memória | Custo |
| --- | ---: | ---: | ---: |
| CatBoost no instante da compra (24 configurações, CC-402) | 16 min 25 s | 347 MB | R$ 0 |
| CatBoost planejamento (CC-407) | 10 min 42 s | 339 MB | R$ 0 |
| CatBoost contexto rico (CC-408) | 19 min 47 s | 371 MB | R$ 0 |
| LoRA Qwen2.5-0.5B (CPU local, CC-503) | 2,70 h | 3,1 GB | R$ 0 |
| LoRA Qwen2.5-1.5B (GPU T4 do Colab, CC-503) | 0,25 h | 6,4 GB VRAM | R$ 0 |

## 5. Interpretador de objetivo (CC-501–504)

Corpus sintético:
- rubrica de 5 saídas;
- teste de 100 frases escrito à mão e selado antes do gerador;
- treino 430 e validação 161.

O gate foi registrado antes do teste: JSON ≥ 0,95, macro-F1 ≥ 0,90 e recall de "pedir
esclarecimento" no máximo 0,05 abaixo das regras.

| Sistema | JSON válido | Macro-F1 | Recall NEEDS_INPUT | p50 CPU | Gate |
| --- | ---: | ---: | ---: | ---: | --- |
| Regras `rules_v1` | 1,00 | 0,918 | 1,00 | < 1 ms | aprovado (padrão) |
| Qwen2.5-0.5B base | 0,89 | 0,189 | 0,88 | 3,3 s | reprovado |
| Qwen2.5-0.5B + LoRA | 1,00 | 0,766 | 0,92 | 3,2 s | reprovado |
| Qwen2.5-1.5B base | 0,76 | 0,560 | 0,56 | 10,2 s | reprovado |
| Qwen2.5-1.5B + LoRA | 1,00 | 0,934 | 0,88 | 10,0 s | reprovado (ambiguidade) |

**O ajuste funciona como engenharia.** Na comparação pareada com as regras, o 1.5B ajustado
acerta 8 frases que as regras erram, e as regras acertam 6 que ele erra (McNemar p ≈ 0,79).

**Mas não é adotado.** O 1.5B ajustado pede esclarecimento menos que as regras e seguiu uma
instrução embutida na frase; também não supera as regras de forma mensurável e custa ~10 s e
~9 GB por frase nesta CPU.

**Revisão humana:** um revisor, às cegas. Teve 82,5% de acordo numa amostra de 40 frases do
treino (κ 0,78) e confirmou os 15 rótulos disputados do teste. O registro público está em
`configs/objective-parser-gate.yaml`.

## 6. Pertinência estratégica — revisão por jogadores (CC-602)

**PENDENTE** até as respostas chegarem. Amostra, ferramenta e análise estão prontas e foram
validadas com dados sintéticos.

- **Amostra:** 60 situações de **2º item principal** (meio de jogo, minutos 7–17), tiradas do
  teste reservado e da validação do planejamento com contexto, cortadas por campeão: Caitlyn
  23, Ezreal 19, Kai'Sa 18. As de 1º item, no início da partida, ficaram de fora porque quase
  não têm contexto para julgar itens situacionais.
- **Itens por situação:** 4 ou 5, cegos e embaralhados: top-3 da popularidade, top-1 do M1,
  compra histórica e um controle legal sorteado.
- **Protocolo (simplificado depois do piloto com os revisores):**
  - 3 blocos disjuntos de 20 com a mesma mistura de campeão;
  - 6 ou mais jogadores, pelo menos 2 por bloco;
  - cada um escolhe o item que compraria (ou "não sei") e pode marcar os que não fazem sentido.
- **Análise prevista:**
  - fração das escolhas por origem do item, com IC por bootstrap de situações;
  - taxa de rejeição por origem (o controle deve ser o mais rejeitado);
  - concordância de Fleiss entre quem responde o mesmo bloco, e acordo par a par;
  - imitação na mesma amostra.
- **Divergência do plano:** a rubrica 0–3 por item foi trocada pela escolha, porque a versão
  completa se mostrou longa demais. O resultado mede preferência e rejeição, não nota por item.


Sem as respostas, este relatório afirma **apenas imitação e desempenho técnico**.

## 7. Aprendizagem (piloto pedagógico)

**Não executado.** O piloto pré/pós com 6–10 voluntários (`docs/learning-design.md`) não foi
realizado nesta entrega.

O que existe é a engenharia da jornada:
- escolha antes da revelação;
- pistas;
- reflexão;
- transferência sem pistas;
- progresso descritivo;
- relatório.

A jornada está validada por testes de interface (teclado, 360 px, sem rede). **Nenhuma melhora
de habilidade é afirmada.**

## 8. Qualidade de engenharia

| Verificação | Resultado (CI no SHA final do CC-603) |
| --- | --- |
| Backend (unidade, contrato, integração em PostgreSQL efêmero) | 1.421 aprovados, 3 pulados (fixtures privadas fora do contexto Docker) |
| Frontend Vitest | 177 aprovados |
| Playwright com API simulada | 65 aprovados |
| Demonstração na stack real (compose) | 1 aprovado: importação → estudo → texto → reflexão → julgamento → relatório → remoção |
| Rede | Testes do backend bloqueiam qualquer conexão externa; stack real sem host externo |
| Contratos | OpenAPI versionado = app; tipos TypeScript sem drift |

## 9. Ameaças à validade

- **Amostra pequena e correlacionada:** o teste tem 89 decisões em ~57 partidas, metade com
  jogadores vistos no treino. Os intervalos são largos, e diferenças de 0,01–0,02 em MRR não
  são detectáveis.
- **Um patch e um recorte:** 16.19, rota inferior, três campeões. Validação e teste caem no fim
  do patch, quando 90% das partidas são dos três últimos dias.
- **Amostra de conveniência:** a coleta em bola de neve a partir de contas com consentimento
  puxa para jogadores desses campeões.
- **Imitação ≠ pertinência:** todas as métricas de ranking medem concordância com a compra
  histórica, que pode ser ruim. A pertinência depende do CC-602.
- **Teste de planejamento consultado duas vezes** (CC-407 e CC-408); a conclusão do CC-408
  foi tomada na validação.
- **Regras empíricas de inventário:** são hipóteses confirmadas estatisticamente num patch, não
  semântica oficial da Riot. A correlação de undo foi reprovada e segue desligada.
- **Interpretador:** corpus, regras e teste do mesmo autor (o modelo), com um revisor humano.
  As frases são sintéticas, não de jogadores reais.
- **Expurgo:** remover dados não desfaz o aprendizado de modelos já treinados nem recontagem de
  agregados versionados (declarado na interface).

## 9.1 Treino em escala com dez atiradores (datasets v4)

Os três experimentos foram executados em GPU no Colab. A integridade dos datasets, catálogos, modelos e relatórios foi conferida por SHA-256. A seleção registrada usa exclusivamente validação; a análise local não repetiu treino ou teste.

| Tarefa | MRR B0 no teste | MRR M1 completo no teste (IC 95%) | MRR validação B0 / M1 |
| --- | ---: | --- | --- |
| Compra imediata | 0,787471 | 0,969864 [0,964633–0,974981] | 0,779867 / 0,972613 |
| Planejamento | 0,778840 | 0,775527 [0,762696–0,788189] | 0,768605 / 0,765126 |
| Planejamento com contexto | 0,778840 | 0,774994 [0,761676–0,787561] | 0,768605 / 0,763440 |

São 2.381 decisões de teste na compra e 2.394 no planejamento. O gate exige o IC do modelo inteiro acima do ponto do B0 e concordância da validação: passa em compra e falha nos dois planejamentos. Retirar economia na compra reduz MRR a 0,848615, ainda acima do B0; essa ablação não muda a âncora temporal e não representa planejamento. O ganho não pode ser atribuído exclusivamente ao ouro.

No subconjunto de jogadores ausentes do treino (1.196 decisões de planejamento), MRR B0 é 0,761802, M1 0,763991 e contexto 0,766136; os ICs dos modelos incluem o ponto do B0. O maior corpus não demonstrou ganho de planejamento. O produto mantém a popularidade exportada anteriormente, sem promover os modelos nem trocar seu artefato pelo B0 do v4.

Treino e teste somaram 1 h 44 min 14 s, sem contar instalação e benchmark. Permanecem os limites de um patch, amostra em bola de neve e jogadores compartilhados; treino em GPU difere de CPU. Houve acesso anterior ao B0 do teste em um smoke, declarado antes do retorno. Os relatórios recebidos não permitem comprovar ausência de reexecuções remotas. Métricas continuam medindo imitação, sem provar qualidade estratégica.

## 9.2 Planejamento tardio (CC-701)

Dataset próprio de 5.004 decisões legais com âncora anterior ao alvo e features
pré-âncora, sem ouro. Do corpus16.19 v3:3.538/720/746 linhas; suporte mínimo de60
linhas e10 partidas de treino por célula retém3.208/650/672. São19 células: nove
campeões no3º e4º, apenas Jinx no5º; Ezreal sem suporte. Não equivale à cobertura
sobre todas as partidas. Teste já usado antes, jogadores compartilhados não medidos.

Comparação com B0 campeão/ordem e Bbuild condicionado à build prévia. CatBoost
PairLogit200 árvores, profundidade4/6 selecionada pelo MRR do4º na validação,
modelo com composição pública e ablação sem composição. Arrays float32, GPU no
Colab, CatBoost1.2.10, NumPy2.5.3; mesmas features compartilhadas na inferência.

| Ordem no teste | n | B0 | Bbuild | M1 com composição | M1 sem composição |
| --- | ---: | ---: | ---: | ---: | ---: |
| 3º |438|0,746258|0,781084|0,735915|0,749313|
| 4º |217|0,654894|0,653742|0,662953|0,668829|
| 5º |17|0,530758|0,589582|0,636720|0,569307|

Valores MRR de imitação. Desfecho principal4º: B0 escolhido pela validação;
delta M1-B0=+0,008059, IC95% pareado por partida[-0,029796;0,040814]. A validação
teve+0,022308, mas IC[-0,010244;0,055186]. Ambos os ICs incluem zero. Hit@3 do4º
cai81,106%→78,802% (176→171 de217), além da tolerância de2p.p. Composição não
acrescentou ganho frente à ablação (MRR0,668829). Gate técnico reprovado: nenhum
modelo ou scope tardio promovido. Bbuild no3º é observação secundária, não motivo
para selecionar outro critério após teste;5º é exploratório de um campeão.

Execução remota211s, picoRSS3079,6MiB do processo, sem contar instalação nem memória
total do Colab/VRAM. Retorno conferido por hashes de pacote, dados, seleção e
modelos; isso não atesta execução independente nem teste virgem. Inferência local
restrita a19 casos de validação para material de revisão cega; nenhum treino ou
reavaliação local do teste. Revisão estratégica tardia continua pendente.

O negativo vale para esta representação: composição estática e inventário próprio,
sem contexto tático/ameaças dinâmicas observáveis. Não demonstra inutilidade do
estudo situacional, pertinência das compras populares ou efeitos na aprendizagem.

## 9.3 Todos os participantes do 16.19 (CC-907, F0)

Primeira fase da revisão de partida para todos os campeões. Mesmo caminho do produto
(parser, reducer v12 com as regras do 16.19, seleção e candidatos), sem seleção de campeões:
os 10 participantes de cada partida de treino e validação do corpus 16.19 v3 (9.062
partidas), ordinais 1 a 5: 82.753 decisões prontas, de 172 campeões. Ordinais 1–2 seguem a
elegibilidade do produto; 3–5, a regra da CC-701 (único motivo: ordinal além do 2º).
Avaliação pré-registrada antes das métricas, idêntica à CC-804 (popularidade campeão ×
ordinal ajustada só no treino de cada rota, uniforme exato, suporte ≥ 20, bootstrap pareado
por partida, 2.000 réplicas, semente 401; positivo com cobertura ≥ 70%, ≥ 100 decisões de
≥ 30 partidas e IC acima de zero). Só validação; o teste continua fechado; 25 comparações
exploratórias, sem correção para comparações múltiplas.

| Rota | Item | Prontos / pontos (val.) | Cobertura | MRR B0 | MRR uniforme | IC95% B0 − uniforme | Resultado |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Topo | 1º | 2212/3140 | 95,3% | 0,764 | 0,070 | [0,680; 0,706] | positivo |
| Topo | 2º | 1512/2796 | 92,8% | 0,638 | 0,115 | [0,504; 0,540] | positivo |
| Topo | 3º | 889/1965 | 89,2% | 0,659 | 0,195 | [0,441; 0,486] | positivo |
| Topo | 4º | 327/877 | 77,4% | 0,662 | 0,306 | [0,320; 0,393] | positivo |
| Topo | 5º | 92/327 | 28,3% | 0,710 | 0,489 | [0,152; 0,288] | sem amostra |
| Selva | 1º | 2368/3419 | 98,6% | 0,745 | 0,055 | [0,678; 0,703] | positivo |
| Selva | 2º | 88/2789 | 37,5% | 0,590 | 0,078 | [0,432; 0,589] | sem amostra |
| Selva | 3º | 0/2189 | — | — | — | — | sem amostra |
| Selva | 4º | 0/1157 | — | — | — | — | sem amostra |
| Selva | 5º | 0/497 | — | — | — | — | sem amostra |
| Meio | 1º | 2036/3187 | 95,0% | 0,794 | 0,066 | [0,715; 0,740] | positivo |
| Meio | 2º | 206/2935 | 48,5% | 0,670 | 0,110 | [0,513; 0,607] | negativo |
| Meio | 3º | 55/2008 | 21,8% | 0,601 | 0,187 | [0,313; 0,516] | sem amostra |
| Meio | 4º | 15/966 | 0,0% | 0,454 | 0,318 | [-0,030; 0,336] | sem amostra |
| Meio | 5º | 3/354 | 0,0% | 0,556 | 0,719 | [-0,250; 0,000] | sem amostra |
| Atirador | 1º | 2337/3150 | 98,1% | 0,766 | 0,059 | [0,695; 0,719] | positivo |
| Atirador | 2º | 1190/3087 | 95,6% | 0,795 | 0,074 | [0,703; 0,738] | positivo |
| Atirador | 3º | 645/2350 | 93,0% | 0,759 | 0,111 | [0,623; 0,673] | positivo |
| Atirador | 4º | 286/1382 | 93,4% | 0,709 | 0,207 | [0,461; 0,541] | positivo |
| Atirador | 5º | 113/623 | 80,5% | 0,661 | 0,341 | [0,267; 0,377] | positivo |
| Suporte | 1º | 5/3127 | 0,0% | 0,617 | 0,049 | [0,173; 0,951] | sem amostra |
| Suporte | 2º | 0/2524 | — | — | — | — | sem amostra |
| Suporte | 3º | 0/1114 | — | — | — | — | sem amostra |
| Suporte | 4º | 0/302 | — | — | — | — | sem amostra |
| Suporte | 5º | 0/19 | — | — | — | — | sem amostra |

Participações com pelo menos uma decisão pronta: Topo 70%, Selva 74%, Meio 64%, Atirador 74%, Suporte 0%. O que trava Selva, Meio e
Suporte é inventário incerto, não falta de amostra: o pet da Selva some sem substituto
entre 12 e 16 min; no Meio, a bota tier 2 comprada depois do fim da missão vira o sucessor
no mesmo instante; no Suporte, a cadeia do item de missão (3865 → 3866 → 3867 → escolha
final) quase nunca tem evento de compra; desfazer compra segue sem regra validada em todas
as rotas. Propostas de regra foram registradas para confirmação futura; nenhuma regra,
escopo ou produto mudou. Popularidade descreve imitação, não qualidade da compra.

## 10. Limitações e próximos passos

- **Pertinência:** concluir a revisão de especialistas (CC-602) e reportar a concordância.
- **Dados:** a coleta em escala do 16.19 e o treino v4 foram concluídos; o negativo do planejamento permanece. Outros patches exigem nova confirmação pré-registrada das regras de inventário.
  - Ampliar os campeões no corpus atual já foi medido (§3.6). Aumenta a amostra, mas não muda
    a conclusão.
  - O recorte já foi promovido no produto (CC-704).
- **Interface:** guia por campeão (CC-307) implementado como observação descritiva das partidas importadas, separado dos datasets e modelos de avaliação. O mínimo de cinco participações completas é uma regra de apresentação, não uma evidência de validade estratégica. Painel com estatísticas pessoais (CC-309) implementado e validado localmente.
- **Late game:** experimento CC-701 executado (§9.2), sem ganho suficiente no4º item;
  ampliação no produto não liberada. Revisão cega específica está preparada, sem
  respostas humanas. O diagnóstico histórico do corpus menor e o proxy por tags
  de tanque não estabelecem efeito contextual; a medição maior não confirmou esse
  sinal de forma robusta. Qualquer nova experiência exige protocolo separado.
- **Interpretador:** uma nova rodada de ajuste exigiria teste novo, mais exemplos de injeção,
  frases vagas e exclusões, e decodificação restrita com abstenção.
- **Todos os campeões (F0, §9.3):** a referência já é sólida no Topo (1º–4º), no Atirador
  (1º–5º, todos os campeões) e no 1º item de Selva e Meio; o resto depende de regras de
  inventário novas (pet da Selva, bota do Meio, cadeia do Suporte, desfazer).
- **Aprendizagem:** o piloto pedagógico com voluntários, se houver recursos e aprovação
  institucional.

## Referências

Os métodos citados são os mesmos da seção "Métodos usados nos resultados" de
[references.md](references.md):
- CatBoost (ranker);
- bootstrap de partidas (intervalos);
- silhueta (clusters);
- LoRA e Qwen2.5 (interpretador);
- McNemar (comparação pareada);
- kappa ponderado de Cohen (concordância entre revisores).

As fontes da Riot sobre APIs e políticas também estão lá.
