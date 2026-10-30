# LLM no ambiente normal

O interpretador local pode atender à API do projeto Docker `riftlab`, com as
partidas, catálogos e interface habituais. Ele interpreta a prioridade desejada;
o pipeline existente continua gerando e ordenando itens legais. A sugestão só
vale depois da confirmação na interface.

## Preparar

Usar os artefatos e pré-requisitos de [inferência local](inferencia-local.md).
O banco precisa estar previamente preparado para a versão atual da aplicação.
O script abaixo não restaura backups, aplica migrations ou insere demonstrações.
Manter backup privado antes de alterar um banco existente.

`scripts/local_llm.sh` compõe `compose.yaml`, o override privado opcional
`.private/compose.normal.local.yaml`, a configuração de conta existente
`.private/compose.account.yaml` e `compose.local-llm.yaml`. O override privado
pode apontar API e worker para uma cópia de trabalho do banco e para seus dados
brutos locais. Para escolher outro arquivo, exportar
`RIFTLAB_NORMAL_COMPOSE_OVERRIDE` nos terminais usados pelo script. Um caminho
explicitamente informado e inexistente causa erro.

Preservar o segredo de pseudonimização e a configuração da conta associados aos
dados existentes. Credenciais e caminhos da máquina permanecem privados. Para
trabalhar apenas com dados já importados, configurar `RIFTLAB_RIOT_API_KEY` como
vazia no override da API e do worker. Consultas de atualização à Riot ficam
indisponíveis nessa modalidade; o histórico local continua acessível.

Encerrar a demonstração antes de subir o ambiente normal, pois ambos usam as
portas 8000 e 5173. O comando não apaga seus volumes:

```bash
scripts/local_llm_demo.sh down
scripts/local_llm.sh config
```

Se as imagens ainda não estiverem construídas ou o código tiver mudado, executar
`scripts/local_llm.sh build` com o modelo desligado.

## Iniciar

Em um terminal WSL na raiz do repositório:

```bash
scripts/local_llm.sh model
```

Aguardar `Modelo local pronto` e manter o terminal aberto. O manifesto padrão é
`.private/experiments/objective-local-v3/runtime-manifest-windows.json`; outro
manifesto pode ser escolhido com `RIFTLAB_LOCAL_MODEL_MANIFEST`.

Em outro terminal, na mesma raiz:

```bash
scripts/local_llm.sh up
scripts/local_llm.sh check
scripts/local_llm.sh status
```

`check` exige uma interpretação real com `provider: LOCAL_MODEL` e prioridade
`SURVIVAL`. Abrir **http://127.0.0.1:5173/historico**, usar as partidas reais e
abrir uma decisão elegível. Em **Praticar decisão**, descrever o objetivo,
selecionar **Interpretar texto** e confirmar em **Usar este objetivo**.

Os ícones oficiais e nomes de itens em português usam o Data Dragon público,
com o catálogo correspondente à versão da partida. Esse carregamento precisa
de internet, mas não de chave Riot. Partidas sintéticas têm catálogo próprio e
não representam a apresentação completa de uma partida real.

## Memória e encerramento

A sessão do modelo dura uma hora por padrão. É possível reduzir a duração com
`RIFTLAB_LOCAL_MODEL_DURATION`, em segundos, até 3600. O monitor mantém os limites
de pelo menos 2 GiB livres no Windows e no máximo 2 GiB de RSS do modelo. Se ele
encerrar a inferência, a aplicação continua com a seleção manual do objetivo.
Não reduzir esses limites para forçar execução. Restaurações e builds podem
consumir memória e cache adicionais; terminar essas operações antes do modelo.

Para usar o interpretador por regras na API:

```bash
scripts/local_llm.sh rules
```

Encerrar também o modelo com Ctrl+C no terminal dele. Para conectar novamente,
iniciar o modelo e repetir `up` e `check`. Para parar o aplicativo:

```bash
scripts/local_llm.sh down
```

Esse comando preserva os volumes. Ele não encerra o processo externo do modelo.
Não executar duas sessões de modelo no mesmo socket; usar as orientações do
[roteiro da demonstração](roteiro-teste-local-llm.md) para sessões interrompidas.
