# Avaliação do gate v3 no Colab

O [gate v3](../../../configs/objective-parser-gate-v3.yaml) registra antes das métricas os
limiares, a ordem dos sistemas, os adaptadores e os hashes do teste e do código. O backend
continua com `rules_v1` enquanto os resultados não forem auditados e a decisão aplicada.

## Ambiente e limites

As regras v1/v2 usam a CPU de uma sessão Colab; os LoRAs v2/v3 usam a GPU T4 dessa sessão.
A base Qwen2.5-1.5B-Instruct tem revisão fixa, a geração é greedy, float32, seed 0,
quatro threads e até 12 tokens novos. As versões de bibliotecas são verificadas; o torch
CUDA pré-instalado (mínimo 2.6), Python, GPU, CPU e CUDA efetivos são registrados antes do
primeiro sistema. Uma mudança de ambiente entre sistemas interrompe o protocolo.

A latência p95 precisa continuar em até 15 segundos, mas representa esse Colab. Ela não
demonstra desempenho no desktop nem pode ser comparada diretamente à latência CPU dos
gates anteriores. Trocar o provedor para um LoRA continua exigindo decisão e ambiente de
atendimento definido, mesmo que passe nos limiares.

São 296 exemplos no teste principal. As quatro ambiguidades reais com leituras B/N ficam
fora do pacote de avaliação, sem rótulo único; não foram descartadas nem migradas para
treino/validação. A seleção pode tornar o teste principal mais fácil e deve constar dos
resultados. Nenhum ajuste de regra, candidato, treinamento ou limiar é permitido após o teste.

## Gerar a entrega depois do registro e da CI verde

No commit exato de registro, sem alterações nos arquivos congelados:

```bash
python ml/objectives/colab/build_gate_v3_notebook.py \
  --registration-commit SHA_COMPLETO --ci-run ID_DA_CI_VERDE
```

O Python precisa de PyYAML; pode ser o venv de `ml/objectives`. A saída padrão é local,
em `.private/colab/sprint14-cc1404/`, com notebook, ZIP e `SHA256SUMS`. O pacote contém somente
o código congelado, gate/configs, teste sintético e os dois adaptadores selecionados. Não
contém credenciais, conta Riot, banco, dados de jogadores, respostas humanas privadas,
treino, validação ou os quatro casos diagnósticos. O notebook inclui os hashes do pacote.

No Colab, selecionar GPU T4 e executar as células em ordem. O upload solicitado é somente
`riftlab-cc1404-colab-input.zip`. O Drive precisa ser montado para salvar o registro em
`MyDrive/RiftLab/cc1404/<SHA-do-gate>/`. O notebook confirma pela API pública do GitHub a CI
verde no commit informado e compara o gate com os bytes publicados antes de avaliar.

Não executar duas sessões desse notebook em paralelo. Cada sistema roda uma vez, em
processo separado, com saídas/logs persistentes. Executar as células novamente reutiliza
resultados concluídos, verificando seus hashes. Uma interrupção sem conclusão bloqueia
retomada automática. Falhas técnicas sem métricas precisam ser revisadas e documentadas;
somente então o runner admite `--retry-technical NOME_DO_RUN`. Se houve métricas, não há retry.

O comparador deste runner lê os quatro limiares do gate, inclusive latência. O comparador
histórico `riftlab_objectives.compare` não aplica o limite de latência e não decide este gate.

Ao terminar, devolver `riftlab-cc1404-resultados.zip` para auditar métricas, predições,
ambiente, logs e decisão. Em caso de falha, executar somente a célula final de exportação
para obter o mesmo retorno com os registros disponíveis; não apagar a pasta do Drive.

## Verificar a infraestrutura sem consultar o teste

```bash
python -m unittest discover -s ml/objectives/colab -p test_gate_v3_runner.py -v
```

As verificações usam fixtures fictícias: bloqueio de repetição, recuperação de métricas
sem reexecutar, preservação de tentativas técnicas, integridade, aplicação da latência e
compilação das células. Não carregam modelo nem executam sistemas no corpus reservado.
