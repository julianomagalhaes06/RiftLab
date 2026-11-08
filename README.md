# RiftLab

Plataforma web de revisão pós-partida de League of Legends, focada nas **decisões de compra de
itens**. Projeto acadêmico da disciplina de Análise e Projeto de Sistemas.

## O problema

Copiar a build mais popular não ensina o jogador a decidir. A compra certa depende da
composição dos times, da função, do inventário e do momento da partida. Iniciantes e jogadores
de elos mais baixos raramente têm como rever essas escolhas depois do jogo, comparar
alternativas e entender por que uma opção fazia mais sentido.

## A proposta

O RiftLab reconstrói cada partida concluída, encontra os momentos em que o jogador comprou um
item importante e compara essa escolha com até três alternativas. Cada alternativa vem com
justificativa e com os limites da evidência. A proposta é desenvolver autonomia: o jogador
observa o contexto e decide antes de ver a resposta. Não é uma "build pronta" e não promete
vitória.

## Funcionalidades

- **Acesso aberto:** sem conta, qualquer pessoa busca jogadores, abre partidas, acompanha a
  partida ao vivo e vê os padrões por campeão. A conta local serve para analisar compras e
  guardar o estudo, o Painel e a posição da conta Riot vinculada.
- **Conta por e-mail:** cadastro confirmado por um código enviado ao e-mail e recuperação de
  senha pelo mesmo caminho.
- **Conta e histórico:** busca de qualquer jogador por Riot ID (Nome#TAG), com ícone, nível e
  elo, e histórico das partidas ranqueadas atualizado automaticamente.
- **Revisão da partida:** placar com os nicks dos jogadores, trajeto de compras por ida à loja,
  ordem de habilidades, gráfico de ouro, momentos-chave e chance de vitória ao longo do jogo.
- **Análise de decisão de compra:** estado do inventário e da partida no momento da compra e
  até três alternativas comparadas, com custo, justificativa e confiança.
- **Modo estudo:** o jogador escolhe antes de ver a análise, pode pedir pistas e registra uma
  reflexão sobre a decisão.
- **Objetivo estratégico:** o jogador descreve o que queria fazer na partida, por formulário ou
  em texto livre, e a comparação se ajusta a esse objetivo.
- **Painel:** taxa de vitória, KDA, partidas recentes, campeões mais jogados e posição
  ranqueada da conta.
- **Campeões:** padrões de compra observados por campeão e por etapa da build.
- **Partida ao vivo:** os dois times da partida em andamento, na ordem das rotas, com
  campeão, feitiços, runas e elo de cada jogador. A página do jogador avisa quando ele está
  jogando naquele momento.
- **Relatório:** exportação da análise com as evidências e os limites considerados.

## Como funciona

A aplicação importa as partidas pela API oficial da Riot Games, reconstrói o estado de cada
jogador minuto a minuto e gera as alternativas a partir do catálogo de itens do patch da
partida. Um modelo treinado com partidas reais ordena as alternativas, e uma interface web
apresenta a revisão.

Tecnologias: Python (FastAPI), PostgreSQL, React com TypeScript, Docker.

## Executar a demonstração

A demonstração usa partidas sintéticas e roda sem chave da Riot e sem internet depois da
primeira instalação. Requer Docker com Compose v2.

```sh
scripts/demo.sh up     # abra http://127.0.0.1:5173
scripts/demo.sh down
```

Para testar o interpretador quantizado na RX 7600, seguir o
[roteiro local com LLM](docs/roteiro-teste-local-llm.md). Ele prepara uma
demonstração separada e mantém o modelo como opção experimental.

Para conectar o modelo às partidas e à interface habituais, usar o
[guia da LLM no ambiente normal](docs/llm-ambiente-normal.md).

## E-mails da conta

No ambiente com Docker Compose, os códigos de confirmação e de recuperação de senha vão para
o Mailpit, uma caixa de entrada local em http://127.0.0.1:8025: nada sai da máquina. Para enviar
de verdade, preencha as variáveis `RIFTLAB_SMTP_*` no `.env`; o `.env.example` traz o exemplo do
Gmail com senha de app.

## Documentação

- [Requisitos](docs/requirements.md)
- [Arquitetura](docs/architecture.md)
- [Contratos da API](docs/contracts.md)
- [Padrão visual](docs/design-system.md)
- [Detalhes de implementação e entrega](docs/detalhes-entrega.md)

## Relação com a Riot Games

Projeto acadêmico independente, sem afiliação nem endosso da Riot Games. League of Legends e
suas propriedades pertencem à Riot Games, Inc.
