# ADR 0004 — Expurgo por job durável com lápide sem identidade

Status: aceito (Sprint 6, 13/10/2026).

**Contexto.**
- O RF12 exige remover uma partida e seus derivados com confirmação, informando o que é
  afetado e sem prometer desfazer o aprendizado de modelos.
- Arquivos brutos e linhas do banco não podem ser apagados numa única transação.

**Decisão.**
- O expurgo é um job PURGE na mesma fila durável dos outros jobs (lease, retomada, nova
  tentativa), pedido com confirmação explícita: o próprio id.
- **Ordem:** os brutos saem primeiro (operação idempotente, restrita ao diretório, sem seguir
  link). Depois, numa transação única:
  - os dependentes saem em ordem de chave estrangeira;
  - os datasets registrados que listam a partida são invalidados, e os modelos ativos
    treinados com eles são retirados (o registro recusa promovê-los de novo);
  - uma lápide guarda só um HMAC do id;
  - o id também sai da linha do job.
- **Reimportação:** a reimportação real da mesma partida é recusada.
- **Retenção:** só lista por padrão e apaga apenas com `--execute`.

**Consequências.**
- **Retomada:** uma queda entre os passos é retomada sem estado parcial visível.
- **Segredo compartilhado:** API e worker precisam do mesmo segredo local para reconhecer a
  lápide.
- **Limites:** o corpus de pesquisa privado e o agregado de popularidade versionado não são
  alterados automaticamente; isso fica declarado na interface e na documentação.
- **Datasets antigos:** os registrados antes da lista de partidas aparecem como não
  verificáveis.
