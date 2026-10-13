# Interpretador de objetivo — corpus, ajuste LoRA e avaliação

Converte uma frase em português numa prioridade de compra: `BALANCED`, `SURVIVAL`, `DAMAGE`,
`UTILITY` ou `NEEDS_INPUT` (pedir esclarecimento). A saída é sempre o JSON `{"priority": ...}`.
Este diretório é um ambiente Python separado, e o backend não depende dele. O backend usa por
padrão o provedor de regras (`backend/src/riftlab/objectives/rules.py`). O modelo ajustado só
entra pelo sidecar opcional `serve.py`, em loopback, e a saída é validada antes de qualquer uso.

## Conteúdo

- `corpus/`: rubrica (`RUBRIC.md`), teste de 100 frases escrito à mão e selado antes do gerador,
  e treino/validação sintéticos gerados por templates exclusivos de cada split. Os dados são
  sintéticos, sem jogadores reais.
- `riftlab_objectives/`:
  - `prompt.py`: prompt e parser estrito.
  - `train.py`: LoRA, perda só na resposta.
  - `evaluate.py` e `evaluate_rules.py`: avaliação.
  - `metrics.py`: métricas.
  - `compare.py`: tabela e gate.
  - `serve.py`: sidecar HTTP em 127.0.0.1.
- `colab/`: notebook gerado por `build_notebook.py`. Leva o mesmo código e o treino/validação, não
  o teste, para treinar em GPU.
- Configs em `configs/experiments/objective-*.yaml`. Modelos Qwen2.5-Instruct 0.5B e 1.5B
  (Apache-2.0) em revisão fixada.

## Reproduzir

```bash
cd ml/objectives
uv sync                                   # torch CPU, transformers, peft (versões em uv.lock)
uv run python -m riftlab_objectives.train --config ../../configs/experiments/objective-lora-0.5b.yaml
uv run python -m riftlab_objectives.evaluate --config ../../configs/experiments/objective-eval-lora-0.5b.yaml --split val
uv run python -m riftlab_objectives.evaluate_rules --split val
uv run python -m riftlab_objectives.compare --reference rules-val rules-val <run> ...
```

Os resultados vão para `artifacts/objectives/<run>/`, fora do Git: adaptadores, `manifest.json`
com hashes dos dados e do adaptador, predições e resumo. A config de avaliação confere
`adapterSha256` antes de carregar.

## Resultado (teste reservado, 100 frases, CPU i3-7020U, execução única)

O gate foi registrado antes de consultar o teste. Exige JSON válido ≥ 0,95, macro-F1 ≥ 0,90 e
recall de `NEEDS_INPUT` no máximo 0,05 abaixo das regras.

| Sistema | JSON válido | Macro-F1 | Recall NEEDS_INPUT | Latência p50 | Gate |
| --- | ---: | ---: | ---: | ---: | --- |
| Regras (`rules_v1`) | 1,00 | 0,918 | 1,00 | < 1 ms | aprovado |
| Qwen2.5-0.5B base | 0,89 | 0,189 | 0,88 | 3,3 s | reprovado |
| Qwen2.5-0.5B + LoRA (CPU, 2,7 h) | 1,00 | 0,766 | 0,92 | 3,2 s | reprovado |
| Qwen2.5-1.5B base | 0,76 | 0,560 | 0,56 | 10,2 s | reprovado |
| Qwen2.5-1.5B + LoRA (GPU T4, 15 min) | 1,00 | 0,934 | 0,88 | 10,0 s | reprovado (ambiguidade) |

Leitura:
- O ajuste melhora muito os modelos base: JSON válido vai a 100% e o macro-F1 de 0,56 a 0,93 no
  1.5B.
- Ainda assim, o 1.5B ajustado pede esclarecimento menos vezes do que deveria e chegou a seguir
  uma instrução embutida na frase.
- Ele também não supera as regras de forma mensurável (8 × 6 acertos exclusivos em 100 frases).
- Por isso o provedor padrão continua sendo o de regras, e o modelo ajustado fica como
  experimento documentado.
- Limitações: dados sintéticos; regras, gerador e teste do mesmo autor. Um revisor humano
  confirmou às cegas os 15 rótulos disputados do teste e concordou com 82,5% de uma amostra de 40
  frases do treino antes de ver o rótulo (97,5% depois; ver `corpus/RUBRIC.md`).
