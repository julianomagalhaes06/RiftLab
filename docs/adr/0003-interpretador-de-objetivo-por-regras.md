# ADR 0003 — Interpretador de objetivo por regras; modelo ajustado como experimento

Status: aceito (Sprint 5, 12/10/2026).

**Contexto.**
- A ADR 0001 restringia o LLM à interpretação do objetivo. O plano previa um modelo
  pré-treinado com ajuste supervisionado.
- O ajuste LoRA do Qwen2.5-Instruct foi feito: 0.5B em CPU e 1.5B numa GPU T4 do Colab.
- No teste reservado, com gate registrado antes, o 1.5B ajustado chegou a macro-F1 0,934 e
  JSON 1,00. Mas pediu esclarecimento menos que as regras (recall 0,88 contra 1,00) e seguiu
  uma instrução embutida na frase.
- Ele também custa ~10 s e ~9 GB por frase na CPU de referência.

**Decisão.**
- O padrão é o provedor de regras `rules_v1`, sem modelo de linguagem.
- O modelo local fica disponível só por configuração explícita, como serviço em loopback, com
  saída validada de forma estrita.
- O padrão só pode ser uma versão aprovada em `configs/objective-parser-gate.yaml`, travado
  por teste.

**Consequências.**
- A demonstração não depende de modelo de linguagem nem de serviço pago.
- O texto só sugere uma das quatro prioridades e vale depois de confirmado; ambiguidade ou
  falha devolvem ao formulário.
- Uma nova rodada de ajuste exige teste novo e nova aprovação no gate.
