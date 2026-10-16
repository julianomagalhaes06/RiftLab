# Estado técnico do produto

Referência: outubro de 2026. Este resumo permite retomar o desenvolvimento a partir
do clone, sem depender de conversas ou registros locais de execução.

## Funcionalidades disponíveis

- Importação de partidas encerradas BR1, preparação assíncrona e reconstrução do
  inventário anterior à compra, com proveniência e incerteza explícitas.
- Histórico e contexto das partidas importadas, padrões observados por campeão e
  decisões elegíveis com catálogos/regras fixados por versão.
- Análise direta como padrão, com objetivo inicial equilibrado, comparação,
  justificativas, abstenção, cenários, reanálise e relatórios rastreáveis.
- Prática opcional com escolha antes da revelação, pistas, reflexão e transferência.
  Exposição anterior à análise não constitui avaliação pedagógica independente.
- Nomes de itens em português e imagens oficiais do patch, com fallback acessível.
- Painel descritivo da conta configurada e classificação Solo/Duo/Flex separada por
  fila e temporada. Seleção inicial prioriza a melhor classificação disponível;
  seleção manual não é substituída por resposta tardia.

## Limites científicos e funcionais

- A ordem servida usa o critério de referência realmente disponível. Popularidade
  e compra histórica não são gabaritos nem garantia de melhor compra ou vitória.
- Experimentos de ranking contextual e planejamento não autorizaram promoção de
  modelo; resultados negativos e intervalos devem ser preservados. Ver
  [resultados](results.md) e [avaliação](evaluation.md).
- Revisão estratégica por especialistas e avaliação pedagógica com pessoas
  continuam pendentes; implementação e testes automáticos não as substituem.
- Exibir elo Flex não amplia automaticamente o recorte do dataset ou a elegibilidade
  da análise de compras para qualquer fila, função, campeão ou patch.
- Atualização de elo é administrativa e datada, não tempo real. Vincular conta não
  importa automaticamente o histórico de partidas.
- Não há overlay ao vivo, automação de compras ou implantação multiusuário pública.

## Continuidade

Requisitos e contratos estão em [requisitos](requirements.md),
[contratos](contracts.md), [arquitetura](architecture.md) e
[domínio](domain-model.md). Consulte [versionamento](versionamento.md) para saber o
que acompanha o Git e quais dados/configurações devem permanecer privados.
