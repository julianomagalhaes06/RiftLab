# ADR 0001 — Monólito modular e revisão pós-partida

Status: aceito como direção inicial; detalhada pelas ADRs 0002 (critério servido), 0003 (interpretador por regras) e 0004 (expurgo). O monólito modular, o worker separado e a execução em CPU foram mantidos.

Contexto: semestre acadêmico, dados temporais parciais, equipamento doméstico e necessidade de avaliar aprendizagem real.

Decisão: Python para domínio/API/ML, React para revisão, PostgreSQL para estado e jobs; worker separado por processo, mesmo monólito. Modelos tabulares em CPU e LLM restrito à interpretação de objetivo. Dataset e treino fora das requisições. Recorte de uma função/três campeões e alvo de item completo. Aplicação local pós-partida.

Alternativas: Java com serviço Python ampliaria integração sem necessidade inicial; microsserviços acrescentariam operação; LLM escolhendo livremente itens dificulta legalidade e avaliação; RL exige ambiente e validação além do escopo. Clustering terá ablação antes de produção.

Consequências: necessidade de consistência na fila e contratos, limite inicial de uma estação e anotação estratégica manual. Modelos permanecem substituíveis. Reabrir ADR se houver necessidade comprovada de multiusuário, volume concorrente ou ampliação do domínio. Stack não é copiada automaticamente de outro projeto acadêmico.
