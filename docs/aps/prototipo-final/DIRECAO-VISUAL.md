# Direção visual — RiftLab

## Intenção

Uma mesa de revisão pós-partida: o instante da compra é a peça central, com evidência e incerteza ao lado. A interface mostra o que estava disponível **antes** da ação e separa estudo de resultado revelado. Não usa linguagem visual de HUD em tempo real.

## Tokens

| Papel | Cor |
| --- | --- |
| Tinta | `#182B33` |
| Petróleo | `#183F48` |
| Verde de ação | `#126E66` |
| Papel frio | `#F2F5F4` |
| Superfície | `#FFFFFF` |
| Âmbar de atenção | `#9B641F` |

Texto: `Inter` para leitura e controles; números de tempo e patch em `Liberation Sans Narrow` com algarismos tabulares. Sem fonte remota obrigatória. Bordas dividem registros; sombra fica reservada a superfícies elevadas. Raios pequenos nos controles e maiores apenas em painéis externos.

## Layout

```text
desktop
┌ marca ────── Partidas ── Estudo ── contexto da partida ┐
│ enunciado da página           │ dado/ação prioritário  │
├ linha temporal ou lista de decisões ─┬ evidência ───────┤
│ registros em linhas, com tempo real   │ qualidade/fonte  │
└───────────────────────────────────────┴──────────────────┘

mobile
marca / navegação
enunciado
ação prioritária
registros empilhados
qualidade e fonte perto da decisão
```

O alinhamento parte da mesma margem esquerda. O tempo de jogo é a âncora visual de listas de decisões; fotos de campeões, quando houver identificação segura pelo catálogo do patch, ajudam a reconhecer participantes sem virar o centro da análise. Demonstração mantém ilustrações neutras para não atribuir campeão real a IDs sintéticos.

## Revisão crítica

A primeira ideia de sidebar escura, cartões iguais e acento verde reproduzia um painel SaaS comum. Foi substituída por navegação horizontal, linhas de registro e uma faixa de contexto que acompanha o raciocínio pós-partida. Nada neste conceito comunica coleta ao vivo, precisão de ouro ausente ou recomendação validada sem evidência.
