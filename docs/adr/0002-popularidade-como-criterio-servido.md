# ADR 0002 — Popularidade como critério servido; ranker contextual não promovido

Status: aceito (Sprint 4, 09/10/2026; reafirmado em 13/10/2026).

**Contexto.**
- O plano previa um ranker contextual (CatBoost) como critério principal, com a popularidade
  como baseline.
- No instante da compra, o M1 chegou a MRR 0,975, mas o ganho vinha do ouro estimado: só ~4
  dos ~106 itens legais cabem no ouro, e o modelo imitava o momento da compra.
- Sem esse atalho (planejamento do próximo item, CC-407/408), o M1 empatou com a popularidade
  (teste 0,749–0,761 × 0,760), com ou sem contexto rico.
- Clusters não mostraram estrutura (silhueta ~0,08) e foram descartados.

**Decisão.**
- Servir a popularidade por campeão e ordem do item (B0), atrás de uma barreira de validade e
  de um suporte por célula.
- Quando não há referência para a célula, usar as regras explícitas de componentes e custo.
- O registro de modelos continua existindo para versões futuras, mas nenhuma foi promovida.

**Consequências.**
- A tela diz qual critério foi usado e que ele mede o que outros jogadores escolheram, não a
  melhor compra.
- A pertinência estratégica depende da revisão de especialistas (CC-602).
- Reabrir com mais dados (outros patches, janela espalhada) ou com um alvo de pertinência
  anotado.
