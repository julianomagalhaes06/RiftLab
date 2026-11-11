# Padrão visual do RiftLab — Arena vivo

O RiftLab é um site de jogo: a interface deve parecer parte do League of Legends, não um
relatório. Desde 11/11/2026 (pedido do usuário) o padrão "Arena sóbrio" virou **Arena vivo**:
mesma base de tokens e primitivas, com arte dos campeões, ícones, brilho e ouro onde ajudam a
ler e a agir. A origem continua o protótipo da direção C
(`docs/aps/prototipo-competitivo/direcao-c-arena/entrega/`); as referências visuais novas são os
perfis do tracker.gg, u.gg e Mobalytics e as páginas de build do op.gg. Não é preciso seguir o
protótipo ao pé da letra: telas novas podem criar elementos próprios, desde que usem os tokens.

No código, as cores ficam só em `frontend/src/design/tokens.css` e as primitivas em
`frontend/src/design/arena.css`. O teste `design/tokens.test.ts` recusa cor literal fora de
`tokens.css`, variável sem definição e combinação de cor abaixo do contraste AA.

## Princípios

1. **Arte e cor com propósito.** Base escura neutra (`n0`, `n1`, `nline`), com vida onde ela
   carrega informação: splash art do campeão nos cabeçalhos (Início, Campeões, banner do
   Histórico), sempre com véus que garantem 4,5:1 até sobre arte branca; tinta do resultado no
   cartão de partida; reflexo leve no topo das superfícies (`--panel-sheen`); brilho roxo
   (`--glow-accent`) no item escolhido.
2. **Cada cor tem um papel.** Roxo para ação, seleção e destaque da conta própria. Verde e
   vermelho para resultado. Ouro (`--gold*`, como a loja do jogo) para molduras de item, o
   trajeto mais frequente e o número em destaque (`--gold-text`). A cor do elo só na
   classificação. Números sobre arte em placas de HUD (`--hud-bg`).
3. **Dado e ícone antes de texto.** Número grande (Chakra Petch), ícone de linha
   (`shared/ui/GameIcon.tsx`, traço 1,8) e rótulo curto; metodologia e explicações longas ficam
   num "Como ler" (`details`), dica ou na tela de destino, nunca em parágrafos soltos na tela.
4. **Destaque no próprio dado, não em etiqueta.** Como no tracker.gg e no u.gg: o maior número
   da série fica dourado com uma estrela (`.is-best`), a revisão é texto discreto com ponto de
   estado, e informação sobre arte é texto com ícone. Nada de pilhas de pílulas com borda
   colorida; se houver várias marcas, no máximo uma, ou uma fileira uniforme e neutra
   (Mobalytics).
5. **Estado vazio leva a algum lugar.** Ícone, título curto e uma ação (ex.: "Ver no patch
   16.20"); nunca só uma caixa com um parágrafo.
6. **Gamificação só com dado real.** Conquistas, sequências e progresso saem das partidas e
   tentativas carregadas ("Maior KDA", "3 vitórias seguidas", decisões estudadas); nada
   inventado, nada que avalie a decisão sem base.
7. **Nada fictício na interface real.** O protótipo usa dados ilustrativos; o produto mostra só o
   que veio da API. Sem dado, a área mostra o estado vazio, nunca um exemplo.
8. **Tipografia.** Chakra Petch para títulos grandes, nomes de campeão e números; Inter para o
   resto. Rótulos curtos em caixa alta usam Inter com espaçamento (a Chakra Petch abre espaço
   demais depois do "T" em tamanho pequeno).

## Temas

Dois temas com os mesmos papéis de token: escuro (`:root`, padrão) e claro
(`:root[data-theme="light"]`), trocados pelo seletor de sol e lua no topo (`shared/theme`). A
escolha fica no navegador e é aplicada antes da primeira pintura, por um script em `index.html`.
Toda combinação de texto e fundo do claro tem contraste conferido em `tokens.test.ts`. Duas
áreas ficam escuras nos dois temas: o card de elo, uma placa de metal cujo bloco de tokens repete
os valores escuros, e a vitrine do início, com texto sobre a arte (`--hero-*`).

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
| Ao vivo (ponto do botão "Partida ao vivo" com o jogador em partida) | `--live` | `#F2667A` |

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

- **Topo** (64 px, linha `nline` embaixo): marca (hexágono roxo + "RiftLab"); abas com
  ícone de 18 px (Início, Painel, Campeões, Histórico; a antiga aba Minha posição saiu em
  09/11/2026 e o elo ficou no card do Painel), ativa em `ntext` 600 com ícone
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
- **Cartão de partida do Histórico** (refeito em 11/11/2026 a partir dos perfis do tracker.gg,
  u.gg e Mobalytics): superfície com tinta do resultado da esquerda para a direita e borda
  `win-surface`/`loss-surface`, barra de 3 px; fila, tempo, resultado em 15 px e duração;
  retrato de 56 px com a rota; K / D / A em 19 px (mortes em `loss`) e KDA; itens em grade 3 × 2
  com o amuleto redondo ao lado (casas vazias marcadas); participação em abates e farm; os dois
  times em faixas de cinco retratos de 24 px com filete `team-blue`/`team-red` (a conta própria
  com anel `accent`, nomes no texto acessível e na dica); "Analisar →" com a revisão embaixo em
  texto discreto (ponto verde quando há compras revistas) e, na borda direita, a faixa de 44 px
  na tinta do resultado (u.gg) que abre o placar das duas equipes dentro do cartão (tabela por time: campeão, KDA, farm e
  itens; no celular, sem a coluna de itens). Coluna de resumo à esquerda (anel da taxa de
  vitória, KDA, sequência de vitórias, últimos 20 resultados em quadrados V/D, campeões mais
  jogados com taxa e KDA, rotas e o progresso de estudo com atalho para Minha evolução); banner
  do perfil no topo (splash do campeão mais jogado, ícone, Riot ID, elo e "Importar partida");
  destaques no próprio número (`.is-best`, dourado com estrela e o motivo na dica: maior KDA,
  mais farm por minuto, maior participação em abates, com ao menos três partidas); grupos por dia com quantidade, placar e
  KDA médio; filtros em grupos encostados; aviso de atualização numa faixa fina.
- **Banner com splash** (`.splash-banner` em `arena.css`, arte por `shared/ui/SplashArt.tsx`):
  Histórico e perfil do jogador pesquisado. Mostra a splash do campeão mais jogado nas partidas
  carregadas (no perfil, a lista marca o jogador pesquisado como participante local), escuro nos
  dois temas (no claro, os tokens de dentro voltam aos tons escuros, como o card de elo), véus em
  pixels e a sequência de vitórias (`.banner-streak`, chama e número dourados, sem etiqueta)
  quando ≥ 2.
- **Perfil do jogador pesquisado** (`features/players/PlayerPage.tsx`): mesma página do Histórico
  da conta abaixo do banner e dos cards de elo — coluna de resumo (`LoadedSummary` com
  `subject="player"`), busca e filtros de fila e resultado, dias com placar e KDA médio,
  conquistas e o cartão de partida com placar. As peças compartilhadas ficam em
  `features/history/HistoryParts.tsx`.
- **Linha de partida** (outras telas): barra de resultado, "Vitória"/"Derrota" na cor do
  resultado, retrato de 36 px, campeão e fila, data e duração, K / D / A e tropas, ação em roxo.
- **Campeões** (Arena vivo, 11/11/2026): seleção como a do jogo, em cartões com retrato de 72 px
  (escolhido com moldura e brilho roxos; sem partida no filtro aparecem bloqueados, em cinza e não
  clicáveis), patch e etapa numa linha com "Como ler estes números", cabeçalho escuro nos dois
  temas sobre a splash do campeão com nome grande em caixa alta, linha de função e patch com ícone e
  os três números em pílulas com ícone; trajetos em linhas com posição, molduras de slot douradas
  (`.item-slot`), setas e anel de frequência (o 1º em ouro); estado vazio com ícone e ação;
  situações como "Desafios de decisão" com o minuto num círculo e "Analisar decisão" em botão.

No código: card de elo em `features/ranked/RankCard.tsx` (estilo em `rank-card.css`), elos e
escada em `features/ranked/tiers.ts`; Painel em `features/dashboard/Dashboard.tsx`; card de elo
da conta no Painel (Solo/Duo e Flex) em `features/ranked/MyRank.tsx`; página do
jogador (cabeçalho e um card de elo por fila) em `features/players/PlayerPage.tsx`; Histórico
(coluna de resumo, filtros `.segmented` encostados e o cartão de partida em `MatchCard.tsx`, que
a página do jogador também usa) em `features/history/`; Campeões (seletor em retratos,
cabeçalho com splash e trajetos) em `features/champions/`; partida, decisão e importação em `features/matches/` e
`features/imports/`, com `.surface` nos painéis e barra de resultado no `::after` do cabeçalho
da partida (o `::before` da superfície é o fundo). Os selos antigos `.badge`/`.tag` têm a forma
do `.chip`; a classe antiga `.section` (borda no topo) só fica como divisória entre blocos dentro
de uma superfície (estudo, comparação e reflexão da decisão); moldura em
`app/App.tsx`, `app/ContextStrip.tsx` e `features/players/ProfileChip.tsx`.

## Início público (`/`)

Primeira tela do site, aberta sem conta (`features/home/HomePage.tsx`, estilo em `home.css`), no
padrão dos sites de estatísticas do jogo:

- **Vitrine** de largura total (520 a 720 px de altura) com uma splash art oficial do Data Dragon,
  sorteada a cada carregamento da página entre skins curadas (`features/home/splashes.ts`). Véus
  de `n0` com transparência (`--hero-scrim*`) garantem a leitura do texto sobre qualquer arte e
  fundem a base no fundo da página; um brilho roxo discreto (`--hero-glow`) fica atrás da busca.
  Atrás do selo e do título, um véu mais forte (`--hero-scrim-heavy`, .78) e o texto do selo numa
  pílula escura (`--hero-scrim-strong`): medido nas 51 artes da lista, todo texto passa de 4,5:1
  (título, texto grande, de 3:1), e `ui-polish.spec.ts` confere o pior caso, arte toda branca.
  O crédito da skin aparece no canto só depois que a arte carrega; se ela falhar, a vitrine fica
  no fundo neutro.
- **Centro:** selo BR1, título em Chakra Petch (até 60 px) com a segunda frase em roxo, uma frase
  de apoio e a busca grande de jogador (`PlayerSearch variant="hero"`, 58 px, botão roxo
  embutido). No início, a busca do topo some para não repetir.
- **Servidor na busca** (início e topo): seletor à esquerda do campo, como nos sites de
  estatísticas, com a sigla visível e o `select` nativo transparente por cima (teclado e leitor
  de tela intactos). Só o BR está ativo; as outras regiões aparecem como "em breve"
  (`features/players/regions.ts`), porque o backend fala só com os hosts da Riot do BR. Digitar
  só o nome e apertar Enter busca com a TAG padrão do servidor escolhido (BR → `#BR1`).
- **Abaixo:** "Aberto para qualquer pessoa", quatro cartões `.surface.surface--pad` (perfil e
  histórico, revisão da partida, partida ao vivo, campeões), e o bloco "Com conta"
  (`.surface.surface--rank.brackets`) com o que a conta acrescenta e os botões Criar conta / Já
  tenho conta (com sessão: Ir para o Painel / Meu histórico).
- **Celular:** cartões em uma coluna, bloco da conta empilhado, sem rolagem horizontal em 360 px.
- **Exceção aos princípios 1 e 2, só na vitrine do início:** arte de fundo, véus em gradiente,
  brilho e o roxo no título, para a primeira impressão do site remeter ao jogo. As demais telas
  seguem a base neutra.

## E-mails da conta

Confirmação, recuperação de senha e aviso de conta existente (`auth/email_template.py`), com a
mesma identidade do site: fundo `n0`, cartão `n1` de borda `nline` com filete roxo no topo, marca
RiftLab e, em destaque, o código em superfície roxa. Cliente de e-mail não garante fonte própria,
`clip-path` nem SVG: o HTML usa tabelas e estilo inline, com fonte do sistema e sem imagem ou
link. O texto puro vai junto, e o código nunca aparece no assunto nem na prévia da caixa.

## Minha conta

Um quadro único e centralizado na área da página (`.account-panel`, até 520 px, chanfro de elo
e colchetes), sem ser modal: cabeçalho com selo, título e e-mail, um filete, e abaixo o estado do
vínculo (formulário do Riot ID, desafio do ícone ou conta vinculada).

## Sem conta

As áreas da conta (Painel, Histórico, Minha conta, Importar) levam quem não tem
sessão direto à tela de entrar, que volta ao mesmo endereço depois do login (`?next=`). O menu
principal mostra só Início e Campeões; o topo, Entrar e Criar conta. Sair, ou a sessão terminar
no meio do uso, leva ao início. Numa página pública, o detalhe da decisão troca a análise e o
estudo pelo convite `AccountGate` ("Disponível com conta", com Criar conta e Entrar).

## Painel sem vínculo

Conta sem vínculo Riot vê no Painel só um quadro centralizado (`.panel-link`) com o convite para
vincular: sem filtros, métricas ou lista de partidas. O Painel mostra apenas partidas da própria
conta; identificadores técnicos como `BR1_…` não aparecem como título.

## Tela de entrada (contas locais)

Tela própria, fora da moldura do app (`features/auth/AuthScreen.tsx`, estilo em `auth.css`), em
`/entrar`, `/criar-conta` e `/esqueci-senha` (as abas trocam o endereço):

- **Login social:** "Continuar com Google" e "Continuar com Discord" ficam uma vez só, no alto do
  cartão e acima das abas, porque servem para entrar e para criar conta; as abas tratam só de
  e-mail e senha. Conta nova vai à tela "Conta criada" (`/boas-vindas`) ou, vinda de uma página,
  volta a ela com um cartão flutuante no canto (`AccountNotice`), que não desloca a página
  e só sai por ação da pessoa. O mesmo cartão avisa "Google ligado à sua conta" (ou Discord) quando
  o serviço foi ligado a uma conta que já existia com o mesmo e-mail, com um só botão, "Entendi".
- **Topo** de 64 px com a marca (link para o início) e "Continuar sem conta"; **rodapé** com
  créditos e o aviso legal da Riot.
- **Desktop:** duas colunas. À esquerda, contexto (13 px), título em Chakra Petch (40 px), frase e
  três pontos de valor com ícone de check neutro; abaixo, a vitrine com a escada de emblemas
  Ferro → Prata → Ouro → Diamante → Desafiante (`.surface.surface--rank.brackets`). À direita, o
  cartão (`.surface.surface--pad.brackets`) com abas Entrar/Criar conta (`role="tab"`, setas do
  teclado alternam), campos do formulário, botão primário na largura toda e, abaixo, a lembrança
  de que perfis e partidas seguem abertos, com o link "Continuar sem conta".
- **Celular (≤ 960 px):** título curto, cartão logo em seguida e a vitrine no fim; os pontos de
  valor saem. Sem rolagem horizontal em 360 px.
- **Código por e-mail:** depois do cadastro, o cartão troca para "Confirme seu e-mail" (mesmo
  layout, sem abas), com o campo de 6 dígitos (`inputMode="numeric"`, `one-time-code`), reenvio
  com contagem de 60 s e "Corrigir o e-mail". "Esqueci minha senha" usa o mesmo passo, com a
  senha nova e os mesmos requisitos. O foco acompanha o título a cada passo.
- **Login social:** em Entrar e Criar conta, acima do formulário, um botão secundário por
  provedor configurado (largura dividida igualmente) e o separador "ou com e-mail". Os logos são
  imagens da marca de cada serviço (`public/brand`), como os emblemas. Sem provedor, a tela mostra
  só o formulário.
- **Cadastro:** nome, e-mail, senha e confirmar senha; o mostrar/ocultar vale para os dois campos
  de senha. Senha com mostrar/ocultar (`aria-pressed`), requisitos conferidos enquanto a
  pessoa digita (check roxo e texto "atendido/pendente", nunca só cor) e, depois de criar, a
  confirmação "Conta criada" com o próximo passo: "Vincular conta Riot" (primário) ou "Agora não",
  que leva ao início; vinda de outra tela, "Voltar para onde eu estava" passa a ser o primário.
- **Exceção ao princípio 1, pedida pelo usuário (05/11/2026):** a vitrine usa emblemas de elo
  com o halo do card de elo fora do card de elo, como decoração da marca. Não mostra elo de
  ninguém nem dado fictício. Tudo é local (emblemas em `public/ranked-emblems`): a tela não faz
  requisição externa.

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

## Polimento e movimento

Revisão com as skills better-ui e ui-ux-pro-max (09/11/2026), conferida em
`tests/e2e/ui-polish.spec.ts`:

- Controle visualmente menor que 44 px (seletor de tema, mostrar senha, chip de decisão) ganha um
  `::after` transparente até 44 px. O `::after` parte de dentro da borda: com borda de 1 px, o
  recuo é 1 px maior.
- Ao clicar, botões, abas segmentadas, seletor de tema, busca e chips encolhem para `scale: 0.96`
  em 150 ms (`cubic-bezier(0.2, 0, 0, 1)`). Só `scale` tem transição; cores mudam na hora, para a
  troca de tema não animar a página inteira. Movimento reduzido desliga a escala.
- Ícones de 24 unidades usam traço 1.8.
- Imagens de item recebem o mesmo contorno de 1 px do retrato (`--portrait-outline`).
- Cartão flutuante entra em 0,28 s (12 px) e sai em 0,2 s (8 px), ambos `ease-out`.
- O card de elo segue escuro no tema claro e redefine também os apelidos `--color-*`, que
  são resolvidos na raiz.

## Ativos

- Retratos e ícones de invocador: Data Dragon da Riot, na versão do catálogo.
- Splash arts do início: Data Dragon (`cdn/img/champion/splash`, sem versão). Só skins cujo
  endereço responde; as mais recentes ainda não estão lá.
- Emblemas de elo: pacote oficial "Ranked Emblems Latest" (`frontend/public/ranked-emblems/`).
- O rodapé mantém os créditos e o aviso de que o RiftLab não é endossado pela Riot Games.

## Ao criar ou mudar uma tela

1. Montar com `.page-head`, `.surface`, `.section-head` e as linhas acima antes de criar classe nova.
2. Cor nova só como token em `tokens.css`, com papel definido e contraste coberto pelo teste.
3. Conferir 360, 1280 e 1440 px contra o protótipo (espaçamento, hierarquia, cores por papel).
