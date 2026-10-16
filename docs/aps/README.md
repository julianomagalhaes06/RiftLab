# Entrega de APS — visão final do RiftLab

Esta pasta apresenta **como o produto deve funcionar ao fim do projeto**. Requisitos e telas são uma especificação e um protótipo conceitual, não uma declaração de que cada função já está implementada. Partidas, contagens, resultados e versões mostrados nas telas são ilustrativos.

## Arquivos para entrega

| Peça | Arquivo |
|---|---|
| Requisitos funcionais RF01–RF22 | [requisitos-funcionais.tex](requisitos-funcionais.tex) ([PDF](requisitos-funcionais.pdf)) |
| Requisitos não funcionais RNF01–RNF12 | [requisitos-nao-funcionais.tex](requisitos-nao-funcionais.tex) ([PDF](requisitos-nao-funcionais.pdf)) |
| Diagrama de casos de uso UC01–UC09 | [diagrama-casos-de-uso.svg](diagrama-casos-de-uso.svg) (e [PNG](diagrama-casos-de-uso.png)) |
| Fonte semântica UML | [diagrama-casos-de-uso.puml](diagrama-casos-de-uso.puml) |
| Interface adotada (Painel desktop) | [prototipo-competitivo/direcao-c-arena/entrega/RiftLab-painel-desktop.png](prototipo-competitivo/direcao-c-arena/entrega/RiftLab-painel-desktop.png) |
| Pranchas editáveis da interface | [prototipo-competitivo/direcao-c-arena/](prototipo-competitivo/direcao-c-arena/README.md) |

O diagrama tem quatro atores: operador local, jogador, revisor e serviço de dados da Riot (Match-V5 para partidas concluídas, League-V4 somente para a conta própria e Data Dragon para ativos). Este serviço não atua no modo de demonstração. As nove elipses são os casos de uso; as linhas sem seta são associações UML. O SVG tem layout editorial para a apresentação; o PlantUML mantém a definição semântica editável.

## Estado da implementação (13/10/2026)

Esta pasta descreve a **visão final**. O código entregue cobre a maior parte dela; a tabela
separa o que funciona de verdade do que existe só como protótipo ou especificação. Detalhes e
testes estão em `../requirements.md` (rastreabilidade) e os números em `../results.md`.

| Área ou jornada | No código | Só protótipo, especificação ou pendente |
| --- | --- | --- |
| Importação e demonstração (UC01) | Importação BR1 com fila durável, demonstração sem rede, progresso e nova tentativa | — |
| Revisão de compra (UC02) | Estado anterior à compra com proveniência, candidatos legais, comparação de até três com custo restante, popularidade versionada com suporte e abstenção | Ranker contextual não servido (não superou o baseline) |
| Objetivo (UC03) | Formulário e texto por regras com confirmação; modelo local opcional | Modelo ajustado reprovado no gate (experimento documentado) |
| Avaliar e exportar (UC04) | Julgamento por rubrica com abstenção, relatório JSON/HTML sem identificadores | Avaliação cega por especialistas em andamento (CC-602) |
| Operar e remover (UC05) | Experimentos pela CLI com manifesto, registro de modelos, expurgo com confirmação e retenção em dry-run | — |
| Estudo (UC06) | Escolha antes da revelação, duas pistas, reflexão, transferência sem pistas, progresso por contagens | Glossário só no protótipo do CC-102 |
| Painel e Histórico (UC07) | Quatro áreas navegáveis, cinco partidas recentes, Histórico com busca e filtro de origem | Taxa de vitória, KDA e decisões revistas no Painel (CC-309); filtro por resultado |
| Campeões (UC08) | Nome e retrato oficiais do patch no Contexto da partida | Guia por campeão com trajeto e abstenção (CC-307) |
| Minha posição (UC09) | Elo da conta própria por League-V4, com estados de indisponível e não classificado | — |

Partidas, contagens e resultados nas pranchas e nos protótipos continuam ilustrativos.

## Jornada ilustrada

A interface final tem quatro áreas: **Painel**, **Campeões**, **Minha posição** e **Histórico**.

1. Importação de partidas concluídas ou abertura da demonstração offline.
2. Painel com taxa de vitória e KDA do recorte (com o tamanho da amostra), cinco partidas recentes, campeões em foco e o card de elo.
3. Histórico com busca e filtros; “Contexto” abre a partida.
4. Seleção de participante e compra; contexto **anterior** à compra, qualidade dos dados e objetivo confirmado.
5. Escolha em modo Estudo, com pistas opcionais e resposta histórica oculta; revelação explícita e comparação de até três alternativas.
6. Cenário independente de transferência, reflexão, avaliação cega e exportação.
7. Campeões: guia de estudo com padrões observados, amostra e abstenção.
8. Minha posição: elo oficial apenas da conta própria, sem ranking entre jogadores.
9. Operação local, versões e remoção confirmada.

O protótipo adotado está em `prototipo-competitivo/direcao-c-arena/` (Painel redesenhado; Campeões, Minha posição e Histórico ainda serão refinados no mesmo estilo). As telas de `prototipo-final/` continuam como referência do fluxo de estudo (Contexto, Estudo, Comparação, Transferência, Avaliação e Operação), a ser redesenhado no estilo adotado.

## LaTeX e diagrama

Em uma máquina com TeX Live ou equivalente, execute nesta pasta:

```bash
pdflatex requisitos-funcionais.tex
pdflatex requisitos-funcionais.tex
pdflatex requisitos-nao-funcionais.tex
pdflatex requisitos-nao-funcionais.tex
```

Os documentos usam `babel`, `geometry`, `longtable`, `booktabs`, `xcolor`, `fancyhdr` e `hyperref`. O SVG e o PNG do diagrama já estão prontos para inserir na entrega; `python3 build_use_case_diagram.py` os regenera em SVG, seguido de `convert diagrama-casos-de-uso.svg diagrama-casos-de-uso.png` para o PNG.

## Figma

O [arquivo Figma RiftLab — Visão final APS](https://www.figma.com/design/ui9sAZP1Y7nqvx6yx99Ybe) contém as telas de versões anteriores. A interface adotada ainda não foi importada para ele; as pranchas SVG de `prototipo-competitivo/direcao-c-arena/` são a fonte atual.

## Limites de evidência

O protótipo não comprova API Riot, treino, inferência, exportação, desempenho ou implantação; o contraste foi calculado apenas sobre as cores do protótipo. Os documentos definem os critérios a verificar. Nenhum dado bruto de partida, chave, conta ou identificador pessoal integra esta entrega.
