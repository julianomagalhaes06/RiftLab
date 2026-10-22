# Padrão visual do RiftLab — Arena sóbrio

Toda tela do RiftLab, atual ou futura, segue este padrão. A referência é o Painel desktop do
protótipo da direção C (`docs/aps/prototipo-competitivo/direcao-c-arena/entrega/`, gerado por
`gerar.py`: `chrome`, `footer`, `panel_desktop`, `panel_mobile`, `rank_panel`, `match_rows`,
`champion_rows`, `metric_block`). Quando o protótipo não cobre uma tela, ela é montada com os
mesmos tokens e primitivas abaixo, sem inventar elementos novos de estilo.

No código, as cores ficam só em `frontend/src/design/tokens.css` e as primitivas em
`frontend/src/design/arena.css`. O teste `design/tokens.test.ts` recusa cor literal fora de
`tokens.css`, variável sem definição e combinação de cor abaixo do contraste AA.

## Princípios

1. **Base neutra.** Fundo `n0`, superfícies `n1`, linhas `nline`. Nada de gradiente de fundo,
   grade ou brilho fora do card de elo.
2. **Cada cor tem um papel.** Roxo só para ação e seleção (link, aba ativa, foco, item
   escolhido). Verde e vermelho só para resultado (vitória, derrota, ganho e perda de PDL). A cor
   do elo só na classificação (card de elo, linha do elo no chip do perfil).
3. **Dado antes de texto.** Métrica grande, legenda curta; explicações longas ficam em
   `details`, dicas ou na tela de destino.
4. **Nada fictício na interface real.** O protótipo usa dados ilustrativos; o produto mostra só o
   que veio da API. Sem dado, a área mostra o estado vazio, nunca um exemplo.

## Tokens

| Papel | Token | Valor |
| --- | --- | --- |
| Fundo da página | `--n0` | `#0F1013` |
| Superfície | `--n1` | `#16181D` |
| Superfície elevada (hover, controle aberto) | `--n2` | `#1C1F25` |
| Linha de superfície e separador | `--nline` | `#262A32` |
| Linha de selo e retrato | `--nline2` | `#343944` |
| Borda de controle (campo, seleção, botão secundário; ≥ 3:1) | `--nline-strong` | `#656B76` |
| Texto | `--ntext` | `#ECEDEF` |
| Texto secundário | `--ntext-muted` | `#B4B8C1` |
| Texto terciário (legenda, rodapé) | `--ntext-subtle` | `#8C919B` |
| Roxo de texto, link e foco | `--accent` | `#B8A6FF` |
| Roxo de preenchimento (botão, aba ativa, barra de seleção) | `--accent-strong` | `#8B6CFF` |
| Fundo roxo (selo selecionado) | `--accent-surface` | `#221C3D` |
| Vitória / ganho | `--win` | `#3DD68C` |
| Derrota / perda | `--loss` | `#F2667A` |
| Fundo de ganho (faixa de variação) | `--win-surface` | `#0F2E26` |

Por elo há cinco tons, `--tier-<elo>-base|light|dark|deep|text` (`iron`, `bronze`, `silver`,
`gold`, `platinum`, `emerald`, `diamond`, `master`, `grandmaster`, `challenger` e `none` para
sem classificação): `base` é o metal, `light` o reflexo, `dark`/`deep` a sombra e `text` a leitura
sobre o card. Exemplo, Ouro: `#D09A3E`, `#F8DC95`, `#5C4015`, `#2E210C`, `#F2B84B`.

Espaço e forma: chanfro `--cut` 8 px (superfícies), `--cut-chip` 6 px (retrato, placa),
`--cut-rank` 14 px (card de elo); raio `--radius-sm` 4 px só em controles e selos.

## Tipografia

- **Chakra Petch** (`--font-display`, 600): só a marca, o título curto da página (30 px no
  desktop, 22 px no celular) e o nome do elo no card de elo.
- **Inter** (`--font-body`): todo o resto, com números tabulares e sem caixa alta em dados.
  Tamanhos: métrica 44 px/600; título de seção 16 px/600; texto 15 px; dado de linha 14–16 px;
  legenda 13 px; rodapé e selo 12 px.

## Moldura

- **Topo** (64 px, linha `nline` embaixo): marca (hexágono roxo + "RiftLab"); quatro abas com
  ícone de 18 px (Painel, Campeões, Minha posição, Histórico), ativa em `ntext` 600 com ícone
  `accent` e barra de 2 px `accent-strong` embaixo; busca Nome#TAG compacta; chip do perfil da
  conta configurada: selo "BR1", ícone de invocador chanfrado (32 px), Riot ID e, abaixo, emblema
  mini + elo e PDL na cor `text` do elo.
- **Faixa de contexto** (32 px, `n1`): à esquerda o aviso (demonstração quando há dado DEMO;
  servidor e escopo nos demais casos), com ícone de informação; à direita "Patch · Data Dragon".
- **Rodapé** (linha `nline` em cima, 12 px `ntext-subtle`, três colunas): "RiftLab" · o que o
  produto é · créditos de retratos e emblemas; abaixo, o aviso legal da Riot em inglês.
- **Larguras menores**: até 1360 px o topo perde respiro; até 1240 px o chip fica só com o selo e o
  retrato (nome e elo seguem no rótulo acessível).
- **Celular e tablet estreito (≤ 960 px)**: topo com marca, BR1 e retrato; busca em linha própria;
  abas fixas embaixo (68 px, ícone 20 px, barra ativa no topo da aba).

## Primitivas (`arena.css`)

| Classe | Uso |
| --- | --- |
| `.surface` | Superfície chanfrada (8 px) com borda `nline`. `.surface--rank` usa 14 px; `.surface--raised` usa `n2`; `.surface--pad` dá 24/28 px de respiro. |
| `.brackets` | Colchetes de mira de 12 px nos cantos; cor em `--bracket-color` (no card de elo, `dark` do elo). |
| `.page-head` | Linha de contexto (13 px `ntext-subtle`), título curto e frase (15 px `ntext-muted`). |
| `.section-head` + `.arrow-link` | Título de seção (16 px/600) e link roxo "Texto →" à direita. |
| `.row-list` | Linhas na mesma superfície, separador `nline` recuado 16 px. |
| `.result-bar` + `data-result` | Barra de 3 px à esquerda da linha (verde/vermelho; roxo em `.is-selected`). |
| `.is-selected` | Véu roxo de 9% para o item escolhido. |
| `.portrait` | Retrato chanfrado (6 px) com anel `nline2`. |
| `.chip` | Selo de 24 px (BR1, origem, estado); `--accent` e `--win` para seleção e ganho. |
| `.metric__*`, `.metric-secondary` | Métrica principal (rótulo, valor 44 px, legenda, barra de 4 px) e secundária em linha. |
| `.segmented` | Grupo de filtros com `aria-pressed`; selecionado com fundo `accent-surface` e traço de 2 px (marca que não depende só da cor). |

Controles globais: botão primário com `accent-strong` e texto `on-accent`; secundário
transparente com borda de controle; campo e seleção com fundo `n1` e borda de controle; foco
sempre com contorno `accent` de 3 px.

## Componentes compostos

- **Card de métricas** (`.surface`): duas métricas lado a lado com divisória vertical; abaixo, uma
  faixa secundária (ex.: decisões revistas, campeões no recorte com retratos de 22 px).
- **Linha de partida**: barra de resultado, "Vitória"/"Derrota" na cor do resultado, retrato de
  36 px, campeão e fila, data e duração, K / D / A e tropas, ação "Contexto" em roxo. No celular,
  retrato + campeão + resultado e data à esquerda; K/D/A e "Contexto →" à direita.
- **Linha de campeão**: retrato de 36 px, nome + contagem, função e subtítulo, "Ver guia →".
- **Card de elo** (`.surface.surface--rank.brackets`): título em caixa alta 16 px, selo BR1 com
  fundo `deep` e texto `text` do elo, emblema oficial com halo, elo e divisão em Chakra Petch na
  cor `text`, legenda da observação, trilha de placas IV → I (concluída: metal escuro com filete;
  atual: metal claro→base→escuro com ▼; próximas: neutras) com o emblema do próximo elo, barra de
  PDL 0–100 com marcas a cada 25 e meta "Faltam X PDL para …" na cor do elo, e faixa de variação
  (fundo `win-surface` para ganho). Mestre, Grão-Mestre e Desafiante trocam a trilha por "Sem
  divisões · PDL sem teto" e mostram o PDL acumulado.

## Layout e responsivo

- Conteúdo com largura máxima de 1360 px e margem de 40 px (32 px até 1280; 16 px no celular).
- Painel: coluna principal (≈ 880) e lateral (≈ 456) com 24 px entre elas; 40 px entre blocos.
- Celular (≤ 700 px): uma coluna, na ordem de leitura do protótipo (métricas → partidas → card de
  elo → campeões). Sem rolagem horizontal em 360 px.

## Acessibilidade

- Texto ≥ 4,5:1 e borda de controle ≥ 3:1 em todas as superfícies (verificado no teste de
  tokens). Resultado e seleção nunca só pela cor: texto ("Vitória", "Derrota") ou traço junto.
- Alvos de toque ≥ 44 px; foco visível em tudo que é interativo; elementos decorativos com
  `aria-hidden`. O recorte chanfrado corta contornos; itens que encostam na borda da superfície
  usam contorno de foco interno.
- `prefers-reduced-motion` desliga transições.

## Ativos

- Retratos e ícones de invocador: Data Dragon da Riot, na versão do catálogo.
- Emblemas de elo: pacote oficial "Ranked Emblems Latest" (`frontend/public/ranked-emblems/`).
- O rodapé mantém os créditos e o aviso de que o RiftLab não é endossado pela Riot Games.

## Ao criar ou mudar uma tela

1. Montar com `.page-head`, `.surface`, `.section-head` e as linhas acima antes de criar classe nova.
2. Cor nova só como token em `tokens.css`, com papel definido e contraste coberto pelo teste.
3. Conferir 360, 1280 e 1440 px contra o protótipo (espaçamento, hierarquia, cores por papel).
