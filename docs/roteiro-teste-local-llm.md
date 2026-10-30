# Roteiro de teste local com a RX 7600

Este roteiro prepara uma demonstração experimental com banco próprio. A API,
worker, banco e frontend ficam no projeto Docker `riftlab-llm-demo`; os pesos
permanecem no Windows e o sidecar leve fica no WSL. Não é necessário modificar
`.env`, publicar portas adicionais ou contratar hospedagem.

Os comandos de build e inicialização são para uma sessão com o computador
disponível. A validação `config` apenas lê e valida arquivos.

## 1. Conferir os pré-requisitos

Usar o terminal WSL na raiz do repositório. São necessários Docker/Compose,
Python 3.12 ou posterior, interoperabilidade com PowerShell e os artefatos já
convertidos descritos em [inferência local](inferencia-local.md).

O manifesto local padrão é
`.private/experiments/objective-local-v3/runtime-manifest-windows.json`. Em outro
ambiente, definir `RIFTLAB_LOCAL_MODEL_MANIFEST` com o manifesto preparado naquela
máquina. Não é preciso reconverter ou treinar a cada execução.

```bash
scripts/local_llm_demo.sh config
```

Resultado esperado: `Configurações válidas; nenhum serviço foi iniciado.`

Para conferir a CI, abrir a aba Actions do repositório e localizar o SHA desejado.
Se o GitHub CLI já estiver autenticado, também é possível consultar com
`gh run list --commit SHA_COMPLETO`. Não registrar tokens em comandos ou logs.

## 2. Construir as imagens com o modelo desligado

```bash
scripts/local_llm_demo.sh build
```

O primeiro build pode baixar imagens e dependências e consumir mais RAM. Não é
uma medição do consumo de execução. Depois de alterações no código da API,
repetir esse passo antes de testar a imagem nova.

## 3. Iniciar o aplicativo com as regras

```bash
scripts/local_llm_demo.sh baseline
scripts/local_llm_demo.sh status
```

O comando inicia o banco isolado, aplica suas migrations, sobe os serviços e
prepara partidas sintéticas pela API. As credenciais da Riot são desabilitadas
nessa demonstração. O banco de desenvolvimento e os demais projetos não são
alterados. As portas 8000 e 5173 precisam estar disponíveis.

Abrir **http://127.0.0.1:5173**. Conferir a navegação e registrar a RAM utilizada
no Gerenciador de Tarefas do Windows antes de iniciar o modelo. Não somar a RAM
do WSL novamente ao total mostrado pelo Windows.

## 4. Iniciar o modelo em outro terminal WSL

Na mesma raiz do projeto, em um segundo terminal:

```bash
scripts/local_llm_demo.sh model
```

Deixar esse terminal aberto. Aguardar `Modelo local pronto`. O launcher confere
os hashes, usa Vulkan/RX 7600 e cria também o socket
`.private/local-runtime/docker-socket/interpret.sock`.

O modelo tem duração padrão de 30 minutos. Para escolher outra duração, até uma
hora, passar `RIFTLAB_LOCAL_MODEL_DURATION=3600` antes do comando. O monitor encerra
o modelo se detectar menos de 2 GiB livres no Windows ou mais de 2 GiB de RSS no
processo do modelo. Não diminuir esses limites para forçar a inicialização.

Se já houver um socket, o launcher recusa substituí-lo. Encerrar primeiro a sessão
anterior. Para uma sessão interrompida abruptamente, usar uma nova pasta privada
via `RIFTLAB_LOCAL_MODEL_SOCKET_DIR` nos terminais do modelo e da demonstração;
não remover arquivos de uma sessão que ainda esteja em uso.

## 5. Conectar a API Docker e conferir as respostas

Voltar ao primeiro terminal:

```bash
scripts/local_llm_demo.sh enable
scripts/local_llm_demo.sh check
```

Somente a API é recriada. Ela recebe um diretório de socket em modo somente
leitura, sem pesos, binários ou tokens. A imagem usa UID 1000, que deve coincidir
com o dono do socket no WSL. Não usar `chmod 777` para resolver uma divergência.

O comando `check` envia três frases de desenvolvimento e mostra as respostas.
Conferir `provider: LOCAL_MODEL` e
`parserVersion: qwen2.5-1.5b-lora-v3-q4_k_m`. Os testes devem prosseguir somente
depois de investigar qualquer fallback ou falha dessa etapa.

O endereço `localhost` usado pela API é transportado pelo socket Unix; não tenta
encontrar o serviço no loopback de outro contêiner. O sidecar encaminha o pedido
por pipes a um processo Windows, que chama o modelo no loopback do Windows.

## 6. Testar pela interface

No Histórico, selecionar o filtro **DEMO**, abrir uma partida sintética, seu
contexto e uma decisão preparada.
No seletor de objetivo, usar **Ou descreva em uma frase (opcional)** e
**Interpretar texto**. A interpretação só deve valer depois de selecionar
**Usar este objetivo**.

| Entrada de desenvolvimento | O que conferir |
| --- | --- |
| `quero causar mais dano` | Sugestão de Dano e confirmação explícita |
| `quero sobreviver mais` | Sugestão de Sobrevivência |
| `preciso de mana` | Sugestão de Utilidade |
| `quero uma build equilibrada` | Sugestão de Equilibrado |
| `quero dano e sobreviver` | Observar se pede esclarecimento; registrar qualquer interpretação inadequada |

Também verificar se a prioridade confirmada reordena as alternativas e se ainda
é possível escolher o objetivo manualmente. A LLM interpreta o objetivo; ela não
gera itens nem substitui as regras de legalidade e o ranking.

Registrar em uma pasta de `.private/`: versão, frase, resultado, demora percebida,
RAM total do Windows antes/depois e eventuais mensagens de erro. Observar a VRAM
na aba de GPU do Gerenciador de Tarefas. Testar alguns minutos com os aplicativos
habituais abertos, sem repetir o teste reservado v3.

## 7. Conferir a indisponibilidade e voltar às regras

Com a API ainda configurada para o modelo, pressionar Ctrl+C no terminal do
modelo. Tentar interpretar outra frase na interface. O resultado esperado é
abrir o formulário para escolha manual; a página deve continuar funcionando.

Depois, no terminal da demonstração:

```bash
scripts/local_llm_demo.sh disable
```

A API volta a `RULES/rules_v1`. Para retomar a sessão experimental, iniciar o
modelo novamente e executar `enable`.

## 8. Encerrar a demonstração

```bash
scripts/local_llm_demo.sh down
```

Esse comando remove somente os contêineres e a rede do projeto de demonstração;
**não apaga os volumes do banco**. Encerrar também o terminal do modelo com
Ctrl+C caso ainda esteja ativo. Os resultados e logs continuam privados.

## Alcance da validação

A preparação incluiu configuração Compose e testes do transporte HTTP por socket
com geração fictícia. Em 30/10/2026, o roteiro também foi executado com os quatro
serviços Docker, banco separado, fixtures DEMO e o modelo real na RX 7600.

Foram conferidos no navegador o caminho Histórico DEMO → decisão → interpretação
→ confirmação → comparação, o funcionamento com regras e com o modelo, a queda
controlada do modelo e a continuação pelo formulário. As verificações passaram
em 360 e 1280 px, sem erros JavaScript ou requisições externas observadas pelo
navegador. A volta às regras pela recriação da API também foi verificada.

Quatro pedidos pelo proxy real do frontend levaram de 0,2253 a 0,2478 s, com o
modelo já carregado. Essa amostra não estima um p95. O processo do modelo atingiu
404,8 MiB de RAM física; o monitor registrou no mínimo 2361,9 MiB disponíveis no
Windows. Uma amostra dos quatro contêineres somou aproximadamente 350 MiB e não
inclui modelo, sidecar, ponte, navegador ou demais processos do computador.

O runtime executou com UID 1000, código do root somente para leitura, dados
brutos somente para leitura na API e escrita permitida ao worker. A construção
da imagem não precisa trocar recursivamente o dono de código e dependências;
somente o diretório de dados recebe permissão de escrita.

Esses resultados validam o percurso operacional nessa sessão, sem provar uso
prolongado ou qualidade em novos textos. A medição anterior está em
[inferência local](inferencia-local.md). O modelo quantizado permanece experimental.
