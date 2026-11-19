# Late game: compromisso, contexto situacional e explicação com fontes

Este documento descreve como o RiftLab analisa as compras do 3º ao 6º item principal de
qualquer campeão (botas fora da contagem) e como a explicação se apoia em fatos recuperados.

## 1. O momento certo da decisão (compromisso)

O evento `ITEM_PURCHASED` do item completo chega depois da escolha. No corpus 16.19 (16.127
decisões de 1º/2º item de atiradores), no instante em que o item ficava pronto:

| Medida | Valor |
|---|---|
| O item comprado usava componente já possuído | 99,5% |
| Parte do preço já paga em componentes (mediana) | 68% |
| Nenhum outro item aproveitava o ouro já pago | 52,1% |

Analisar esse instante premia "terminar o que começou". Por isso, a partir do 2º item principal,
a decisão é analisada na **âncora de compromisso**: a primeira compra depois do item principal
anterior (`decisions/commitment.py`, `decisions/anchoring.py`, seleção `decision_selection_v3`).
Inventário, orçamento, nível, features e recorte vêm da âncora; o 1º item não tem item anterior
e continua no instante da compra. Depois da revelação no modo Estudo, a compra é classificada
(`FORCED`, `SHARED` ou `OPEN`) e a tela diz quando o caminho começou e quanto já estava pago.
O motor `RULES` deixou de bonificar componentes possuídos (`configs/ranking/rules-v2.yaml`).

## 2. Qualquer campeão: recorte v4 e inventário tolerado

`configs/scope.yaml` v4 (`lateScope.fromOrdinal: 3`): do 3º item principal em diante campeão e
rota não restringem. A reconstrução marca o estado inteiro como incerto ao primeiro evento sem
explicação; no meio, quase sempre botas melhoradas pela missão de rota, e no suporte, Sentinelas
de Controle e a cadeia do item de suporte. `decisions/tolerance.py` tolera a incerteza só quando
todos os motivos são de botas, consumíveis, trinket ou itens concedidos (nenhum deles entra em
receita de item principal) e conta a cadeia do item de suporte pela linhagem.

Validação contra os itens finais oficiais (corpus 16.19 inteiro, 106.620 participantes):
divergência de itens principais de 3,1% sem incerteza, 4,7% com incerteza só tolerável e 11,6%
com incerteza que importa. A tolerância custa 1,6 ponto e recupera meio e suporte.

## 3. Contexto situacional e modelo

`ml/situation.py` calcula, só com o que aconteceu antes da âncora e o jogador vê no jogo: itens
completos dos inimigos e aliados com atributos e mecânicas (`profiles/domain.py`, a partir do
bloco `<stats>` e das descrições do Data Dragon), perfil dos campeões (cura, escudo, controle de
grupo, alcance, dano mágico/físico), placar, quem matou o jogador, objetivos e ouro.

`ml/late_model.py`:
- **Padrão**: frequência do item como k-ésimo item principal, com encolhimento do campeão para a
  rota e da rota para o geral, renormalizada nos candidatos legais.
- **Modelo contextual**: CatBoost com softmax por decisão nos 30 candidatos mais plausíveis, com
  o log do padrão como feature e features de confronto (antecura × curadores, penetração ×
  armadura inimiga, estase × assassinos, etc.). Pesos maiores para o 5º e o 6º item.
- **Lift** = log P(contexto) − log P(padrão). A tela mostra o padrão, a escolha da partida e até
  dois desvios (lift ≥ log 1,5 e probabilidade ≥ 5%), cada um com gatilhos tirados das
  contribuições SHAP e escritos com os números reais (`ranking/late_engine.py`).

Dados: `cc late build-rows` (corpus bruto, sem banco) gerou 109.425 decisões do 16.19: 63.490 /
31.326 / 11.935 / 2.674 do 3º ao 6º item; topo 21.318, selva 25.492, meio 22.562, atirador
30.849, suporte 9.203.

## 4. Avaliação (o que não é óbvio)

`ml/late_eval.py` mede por fatia: ordem do item, tipo da compra e **desvio** (alvo ≠ 1º do
padrão). A frase da tela ("esta partida pede X") é medida como afirmação: taxa observada dos
desvios mostrados contra a esperada pelo padrão. IC 95% por bootstrap de partidas. O gate foi
registrado antes de qualquer métrica (log-perda geral, acerto@3 nos desvios e 5º+ item) e o teste
é lido uma vez por `cc late evaluate-test`; sem aprovação, o produto serve só o padrão
(`LATE_PRIOR`). Resultados em `docs/results.md`.

## 5. Explicação com fontes (RAG)

`knowledge/` monta três fontes por patch: trechos em português de itens e habilidades (Data
Dragon pt_BR, `configs/late/<patch>/knowledge.json`), cartões curados com gatilho verificável
(`configs/knowledge/cards-v1.yaml`) e agregados de situações parecidas tirados do treino, só com
amostra mínima (`cases.json`). Na análise a recuperação é estruturada (ids dos itens, gatilhos,
campeões inimigos); BM25 entra só para pergunta livre.

`POST /api/v1/analyses/{id}/explanation` devolve até 4 frases com as fontes de cada uma e os
fatos usados. Com `RIFTLAB_EXPLAINER=local_model`, o mesmo processo local do interpretador de
objetivo responde pela rota `/explain` (modelo base instruído, adaptador desligado, saída em JSON
restrito); um validador descarta frase que cita fato inexistente, traz número ausente dos fatos
citados ou nomeia item/campeão que eles não nomeiam. Sem modelo ou sem frase válida, a resposta é
a determinística, montada dos mesmos fatos. A explicação nunca muda legalidade, preço ou ordem.

## 6. Elo alto

`cc collect apex` coleta partidas Solo/Duo do patch vigente a partir da escada pública de
Desafiante, Grão-Mestre e Mestre do BR1 (PUUID só em memória; manifesto só com ids de partida e
divisão). Com essas partidas, `cc late build-rows` e `cc late train` produzem a referência de elo
alto do patch.

## Comandos

```sh
cc late profiles --patch 16.19 --data-dragon-version 16.19.1
cc late build-rows --manifest <corpus.json> --item-json <item.json> --patch 16.19 \
  --data-dragon-version 16.19.1 --output .private/dataset/late-16.19-v2.jsonl.gz
cc late train --rows <linhas> --item-json <item.json> --patch 16.19 --output-dir <artefato>
cc late evaluate-test --rows <linhas> --item-json <item.json> --patch 16.19 \
  --artifact <artefato> --preregistration <pré-registro>
cc late publish --artifact <artefato> --patch 16.19
cc late knowledge --patch 16.19 --data-dragon-version 16.19.1 --rows <linhas>
```

O modelo (`model.cbm`) fica em `artifacts/late/<patch>/`, fora do Git, montado na API; o padrão e
o resultado do gate ficam versionados em `configs/late/<patch>/`.
