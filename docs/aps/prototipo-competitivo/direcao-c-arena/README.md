# RiftLab — design adotado

Design adotado para o RiftLab: Painel, Campeões, Ranking e Histórico, em desktop (1440) e mobile (390). Todos os números, perfis, partidas, ranking e trajetos são **fictícios**, como no protótipo A. A faixa “Modo demonstração” aparece em todas as telas.

- `gerar.py`: gera as oito pranchas (`python3 gerar.py`; requer Pillow).
- `C-0N-*-desktop.svg` e `C-0N-*-mobile.svg`: SVGs com grupos nomeados e texto real, retratos embutidos.
- `previews/`: PNGs renderizados com as fontes reais e duas pranchas de visão geral (`00-prancha-desktop.png` e `00-prancha-mobile.png`).

## Linguagem

- **Clima**: HUD de e-sports e cliente de jogo. Fundo azul-noite `#090B12` com grade sutil, painéis com cantos chanfrados, colchetes de mira nos cards de destaque.
- **Cores**: violeta elétrico `#8F6BFF`/`#B9A6FF` como acento, dourado `#F2B84B` para liga e ranking, verde `#34D399` para vitória e vermelho `#FF5C74` para derrota. É bem diferente do ciano arredondado do A.
- **Tipografia**: Chakra Petch (títulos e números, em caixa-alta), Barlow Condensed (rótulos e abas, com espaçamento) e Barlow (texto corrido).
- **Elementos de jogo**: retrato do campeão com brilho e fundo desfocado, emblema oficial de Ouro (rótulo “OURO III”, posição fictícia), barra de XP segmentada, anel de taxa de vitória, pódio 1º/2º/3º, teclas Q/W/E/R, trajeto de itens em slots, cards de partida com borda e gradiente pela cor do resultado e sequência V/D.
- **Navegação**: abas com ícone no topo e indicador violeta com brilho. No mobile, barra inferior fixa com ícones.
- **Estados mostrados**: aba ativa, filtro “Geral”/“Todas”, campeão selecionado, linha “Você”, partida selecionada e painel/folha de contexto aberta.

Contraste (WCAG AA, ≥ 4,5:1): texto 16,8:1; secundário 9,3:1; terciário ≥ 4,9:1 em todos os fundos; violeta-claro 8,8:1; dourado sobre fundo dourado escuro 7,6:1; vitória 9,7:1; derrota 6,2:1; texto do botão sobre o violeta 5,5:1.

Motion sugerido para o protótipo interativo (respeitando `prefers-reduced-motion`): indicador da aba deslizando, barra de XP preenchendo por segmento, linha do gráfico sendo traçada até o ponto atual com pulso no anel, e hover dos cards subindo o brilho da borda.

## Painel redesenhado (versão sóbria)

O Painel (`panel_desktop`, `panel_mobile`) e o cabeçalho, a navegação e o rodapé compartilhados (`chrome`, `chrome_m`, `footer`) usam uma base neutra: tokens `n0`/`n1` (fundo e superfície), `nline` (bordas) e `ntext*` (texto).

- **Cores com papel definido:** roxo `accent` só para ação e seleção; `winN`/`lossN` para resultado; a cor do elo (`TIER_STYLE`) só na classificação.
- **Tipografia:** Chakra Petch só na marca e no título curto. Inter em dados, textos e controles, com números tabulares e sem caixa alta.
- **Estrutura:** cabeçalho compacto sem botão; duas métricas principais e uma faixa secundária; logo abaixo, as 5 partidas mais recentes, com rodapé “Ver as 20 partidas no Histórico” (aba Histórico); coluna direita com o card de elo (`rank_panel(..., surface="n1")`) e os campeões em foco. No mobile a ordem é: métricas → partidas → card de elo → campeões. O gráfico de tendência saiu do Painel.
- **Pendente:** Campeões, Minha posição e Histórico ainda usam o estilo anterior dentro do conteúdo e precisam da mesma revisão. A aba Ranking virou **Minha posição** (decisão de 28/09/2026): a prancha C-03 ainda mostra pódio e tabela de perfis fictícios e deve ser redesenhada para exibir só o elo da conta própria (card CC-310).

## Lógica de elo (segue o ranqueado atual do LoL)

- Elos: Ferro, Bronze, Prata, Ouro, Platina, Esmeralda e Diamante, cada um com divisões IV → III → II → I e **0 a 100 PDL por divisão**. Ao chegar a 100 PDL, sobe de divisão direto (a série de promoção saiu em 2023). Mestre, Grão-Mestre e Desafiante não têm divisões e o PDL não tem teto.
- Painel de posição: emblema, “OURO III”, trilha de divisões (IV concluída, III atual, II e I a seguir, próxima etapa Platina IV), barra de 0 a 100 PDL com marcas a cada 25, “Faltam 24 PDL para Ouro II” e ganho da última vitória (+22 PDL).
- Ranking ordenado por elo, divisão e PDL; a tabela mostra emblema, elo e barra de PDL de 0 a 100. O bloco “Como funciona o PDL” resume as regras e mostra a escada de emblemas de Ferro a Desafiante.
- Todos os elos e PDL são fictícios.

### Card de posição por elo (componente reutilizável)

Em `gerar.py`, `TIER_STYLE` define cinco tons por elo (`base`, `light`, `dark`, `deep`, `text`). `tier_defs()` gera, para cada elo, os gradientes `gPlate-*` (metal da placa atual), `gDone-*` (placa concluída), `gFill-*` (barra de PDL) e `gHalo-*` (halo do emblema). O card é montado por `rank_panel(tier, div, lp, gain)`, que usa:

- `division_plate` para as placas chanfradas. Concluída: metal escuro do elo, filete na cor base e numeral no tom do elo. Atual: gradiente metálico claro→base→escuro, textura escovada, borda clara, bisel (luz em cima, sombra embaixo), brilho suave e marcador ▼. Próximas: escuras e neutras, com numeral legível (7,2:1).
- `division_track` para a trilha IV → I, com o emblema do próximo elo à direita.
- `lp_bar` para a barra: trilho escuro com sombra interna, preenchimento no gradiente do elo com textura leve, ponta destacada e marcas a cada 25 PDL. A meta (“Faltam 24 PDL para Ouro II”) vem de `next_division` e aparece na cor do elo.
- Elos do topo (Mestre, Grão-Mestre, Desafiante) trocam a trilha por uma placa “Sem divisões · PDL sem teto” e mostram o PDL acumulado.

Para mudar o elo, basta chamar `rank_panel(..., tier="Esmeralda", div="I", lp=63)`. `C-05-elos-desktop.svg` mostra o mesmo componente em Ferro, Bronze, Prata, Ouro, Platina, Esmeralda, Diamante e Mestre. Contraste verificado em todos os elos (≥ 4,5:1): texto no card, texto na placa concluída, numeral na placa atual e selo BR1. Sem animação; o único brilho é o halo do emblema, a placa atual e a ponta da barra.

## Assets oficiais

- Retratos: Riot Data Dragon `16.17.1` (`../assets/*.png`).
- Emblema de Ouro: pacote oficial “Ranked Emblems Latest” do portal Riot Developer (<https://static.developer.riotgames.com/docs/lol/ranked-emblems-latest.zip>, arquivo `Rank=Gold.png`). Salvos recortados em `../assets/ranked-emblem-<elo>.png` (400 px, emblema central) e `ranked-emblem-<elo>-mini.png` (140 px, tabela, pódio e escada). O pacote também tem versões com asas.
- Uso sujeito à política “Legal Jibber Jabber” da Riot para projetos de fãs. Ao publicar, incluir o aviso de que o RiftLab não é endossado pela Riot Games. O elo exibido é ilustrativo e não representa classificação real.

## Observações

- Figma fora do escopo por enquanto, por decisão do usuário.
- As pranchas das direções anteriores (A e B) foram removidas.
- `entrega/RiftLab-painel-desktop.png`: Painel desktop em 2x (2880 × 2172) para entrega. As demais telas serão revisadas depois.
