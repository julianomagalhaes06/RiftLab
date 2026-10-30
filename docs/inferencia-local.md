# Interpretador local com pouca RAM

O caminho experimental usa a base Qwen2.5-1.5B-Instruct em GGUF Q4_K_M e o LoRA v3
epoch-2 em GGUF F16, carregado separadamente. O llama.cpp executa no Windows com
Vulkan/RX 7600. Um sidecar Python no WSL mantém o contrato `POST /interpret`, sem
importar torch/transformers nem carregar outra cópia dos pesos.

A comunicação WSL → Windows usa um processo PowerShell persistente com JSON por
stdin/stdout. Esse processo chama o servidor exclusivamente em `127.0.0.1`, com
token temporário. Não depende de uma porta de entrada na interface virtual do WSL,
mudança de firewall, driver, configuração global ou hospedagem externa. O texto do
objetivo não aparece nos logs do sidecar ou da ponte. O servidor de inferência não
recebe banco, partidas, credenciais da Riot ou acesso a ferramentas.

## Medição de desenvolvimento

Medição com Ryzen 5 4600G e RX 7600 de 8 GB, llama.cpp b11960
(`8642541b255bfb5dc39cd351a178be09013a55d6`). Amostra determinística de 40 entradas
de `val-v3`, oito por classe, selecionadas por SHA256 do ID antes da inferência.
Contexto 1024, um slot, quatro threads, batch 128/ubatch 64, 12 tokens, greedy,
penalidade de repetição 1 e cache de prefixo desativado em cada pedido.

| Execução | Pico de RAM física do processo do modelo | p50 por frase | p95 por frase | Acertos |
| --- | ---: | ---: | ---: | ---: |
| Windows/Vulkan, carregamento `none` | 405,2 MiB | 0,2245 s | 0,2508 s | 38/40 |
| CPU/WSL, carregamento padrão | 1644,1 MiB | 2,3054 s | 2,4207 s | 36/40 |

Ambos produziram JSON válido em 40/40 exemplos. Macro-F1 na amostra: GPU 0,9499 e
CPU 0,9014; resultados podem diferir por backend numérico. O processo Windows
também registrou aproximadamente 1365 MiB de memória privada comprometida; esse
número não equivale à RAM física residente. A tabela não soma a ponte, o backend,
os drivers nem os demais aplicativos. Houve pelo menos 2652 MiB disponíveis no
Windows durante a medição isolada da GPU. Inicialização observada: GPU 11,9 s e
CPU 2,0 s; o valor GPU inclui acesso aos arquivos pelo WSL e aquecimento.

Na verificação final, com arquivos no armazenamento nativo do Windows e a rota
real do backend por ASGI, o launcher terminou o carregamento em 6,76 s. A primeira
resposta levou 3,23 s (incluindo inicialização da ponte); nas 40 frases, p50 de
0,1880 s e p95 de 0,2157 s, com os mesmos 38 acertos. O modelo atingiu 428,2 MiB
de RAM residente e 1404,1 MiB de memória privada comprometida. Uma amostra dos
contadores do Windows registrou 1025,8 MiB de memória GPU dedicada e 204,7 MiB de
memória GPU compartilhada; não é medição de pico. A ponte Windows ocupou 84,3 MiB
e o processo de validação com o backend, 88,6 MiB no instante medido. A memória
disponível no Windows permaneceu acima de 2707 MiB. Esses valores têm escopos
diferentes e não constituem uma medição da aplicação inteira.

Os arquivos finais ocupam aproximadamente 940 MiB (base) + 8,3 MiB (adaptador). A
preparação F16 e a quantização foram feitas separadamente: picos de 1781 MiB e
1569 MiB, respectivamente. Não são o consumo permanente do aplicativo.

Essa amostra é de desenvolvimento, não um teste independente nem uma nova
aprovação científica. A variante quantizada com LoRA separado não é equivalente
ao FP32 mesclado aprovado no Colab. O teste reservado v3 não foi reexecutado.
O padrão do produto permanece `RULES/rules_v1`.

## Preparação dos artefatos

Usar os [binários oficiais do llama.cpp b11960](https://github.com/ggml-org/llama.cpp/releases/tag/b11960)
e manter suas DLLs juntas. O ZIP Windows Vulkan x64 conferido tem SHA256
`bac6914b5e83523eb008c6789eaa12669f93fbc196c3b9e8237b6672323b33e9`.
Os pesos permanecem locais e não acompanham o clone.

Converter com os scripts oficiais da mesma versão, em ambiente de preparação
isolado com torch, transformers, sentencepiece e protobuf compatíveis:

```bash
python convert_hf_to_gguf.py BASE_HF --outfile base-f16.gguf --outtype f16
llama-quantize base-f16.gguf base-q4_k_m.gguf Q4_K_M 2
python convert_lora_to_gguf.py --base BASE_HF --outfile lora-v3-f16.gguf --outtype f16 ADAPTER_V3
```

Base HF fixada na revisão `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`. O
`adapter_model.safetensors` de origem deve conferir com
`65c60a9b1b588d8395dcbeb1d523039efd2f1fb6f490c7f316e0066a4f844a28`.
Os SHA256 dos GGUF obtidos nesta preparação foram:

- Base Q4_K_M: `77d191163c1fd3e888a956fd790d0726c633fdb0271283eb297af02d7add3664`.
- LoRA F16: `a85429164c4034136093929928108cf1270383563c9a04066d27d1b9dcb32e12`.

## Inicialização e integração

Para executar a aplicação em Docker, seguir o
[roteiro de teste local](roteiro-teste-local-llm.md), que monta apenas um socket
Unix no contêiner da API e usa uma demonstração com banco separado.

Criar um manifesto JSON local com `parserVersion` e as chaves `server`, `model` e
`adapter`; cada uma contém `path` relativo ao manifesto e `sha256` do arquivo.
`server` aponta para `llama-server.exe`. Usar a versão identificável
`qwen2.5-1.5b-lora-v3-q4_k_m`, sem reaproveitar a identidade FP32.
Preferir os binários e pesos no armazenamento nativo do Windows: a leitura de
pesos via `\\wsl.localhost` também pode ocupar cache de arquivos no WSL, que
consome memória física do host. Não aumentar o limite do WSL como substituto
para medir a RAM livre do Windows.

```bash
python3 ml/objectives/local/run.py \
  --manifest CAMINHO_DO_MANIFESTO.json \
  --output .private/local-runtime/SESSAO \
  --duration 1800
```

O launcher confere os hashes, gera um token privado, carrega o modelo na GPU e
inicia o sidecar em `http://127.0.0.1:8765/interpret`. O diretório de saída deve ser
novo. Encerrar com Ctrl+C ou aguardar a duração configurada, até uma hora. O
monitor verifica RSS do modelo até 2 GiB e pelo menos 2 GiB livres no Windows,
encerrando o processo ao detectar violação. São limites monitorados, não uma
reserva garantida pelo sistema operacional. O monitor inclui o período ocioso.

Para uma instância de desenvolvimento do backend no mesmo WSL, configurar apenas
esse processo com:

```bash
RIFTLAB_OBJECTIVE_PARSER=local_model
RIFTLAB_OBJECTIVE_MODEL_URL=http://127.0.0.1:8765/interpret
RIFTLAB_OBJECTIVE_MODEL_VERSION=qwen2.5-1.5b-lora-v3-q4_k_m
```

Essas variáveis precisam ser exportadas no shell que inicia o backend. Um backend
em Docker tem outro loopback: não usar esse endereço dentro de um contêiner sem
uma configuração de transporte explícita. O override optativo
`compose.local-llm.yaml` usa `RIFTLAB_OBJECTIVE_MODEL_SOCKET_PATH`, sem expor novas
portas. O launcher aceita `--unix-socket CAMINHO` para esse caso. O launcher não
altera `.env` ou Compose automaticamente.

O sidecar aceita um pedido por vez e rejeita imediatamente excesso de concorrência
com 429, evitando fila ilimitada de gerações. O backend existente converte essa
falha, indisponibilidade, timeout ou saída inválida em abertura do formulário.
A confirmação pelo usuário continua obrigatória. `/health` verifica o processo
do sidecar; o modelo precisa terminar o carregamento antes de aceitar inferência.

O ensaio inicial exercitou a rota real do backend por ASGI, o HTTP do sidecar,
a ponte nativa e a GPU, incluindo indisponibilidade e dois pedidos simultâneos.
Posteriormente, o [roteiro com Docker](roteiro-teste-local-llm.md) foi executado
com banco, worker, frontend, API e modelo reais, incluindo a jornada no navegador
e fallback após parada do modelo. Isso não comprova uso prolongado ou avaliação
independente da variante quantizada; o padrão do produto permanece pelas regras.

## Reproduzir as medições

`ml/objectives/local/probe.py --prepare ARQUIVO.json` gera a amostra congelada. O
modo `--server ... --model ... --adapter ... --output DIRETORIO` mede a CPU Linux
com monitor de RSS, prazo total e encerramento do processo ao final. Para GPU,
`benchmark_windows.ps1` recebe esses caminhos e `-Requests ARQUIVO.json`; o modo
`probe.py --windows-results DIRETORIO` recalcula métricas das respostas salvas.
Predições, tokens, manifests da máquina e logs ficam em `.private/`.
