# Guia de anotação e limites do teste v3

Aplicar a [rubrica v1](RUBRIC.md), congelada para este experimento. Concordância entre
anotadores mede acordo com essa convenção; não demonstra uma interpretação universalmente
correta da linguagem, nem qualidade estratégica da compra.

## Divergências e ambiguidades

Uma divergência pode indicar erro de anotação, regra pouco clara ou ambiguidade real.
Não excluir automaticamente frases por falta de consenso. Primeiro verificar a regra e as
leituras possíveis; registrar a decisão antes de avaliar qualquer sistema. Erros de rótulo
podem ser corrigidos. Ambiguidades reais precisam continuar documentadas e acessíveis.

O teste principal v3 contém 296 frases. Quatro frases originalmente classificadas como vagas
admitem tanto pedido de padrão (BALANCED) quanto pedido vago (NEEDS_INPUT). Elas estão em
`ambiguities-v3.jsonl`, com as duas leituras, sem rótulo único. Não entram no macro-F1 nem no
gate principal, não foram inseridas no treino/validação e não foram descartadas da análise.
O conjunto é diagnóstico para uma futura avaliação de como o aplicativo pede esclarecimento;
não existe ainda resultado ou protocolo de aprovação para ele.

O teste principal preserva 106 NEEDS_INPUT, incluindo 25 frases com duas prioridades sem
precedência e 16 pedidos vagos. Separar quatro ambiguidades pode tornar a tarefa mais fácil;
o resultado futuro terá de declarar essa seleção e seus limites. Os 300 casos iniciais tiveram
98,33% de concordância humana (295/300; κ de Cohen 0,9783) antes da adjudicação. O acordo final
não deve substituir essa medida inicial nem ser apresentado como prova de correção.

## Roubo de vida: intenção versus meio

Pela convenção da rubrica, distinguir o foco em sustentação de duas intenções coordenadas:

| Frase | Orientação |
| --- | --- |
| “quero me curar batendo” | SURVIVAL: bater é o meio para obter sustentação, não um segundo objetivo declarado. |
| “quero bater e me curar” | NEEDS_INPUT: duas intenções sem precedência, conforme a convenção explícita da rubrica. |
| “lifesteal pra bater e me curar” | NEEDS_INPUT no teste principal; a leitura de ataque como meio para cura é plausível, mas a adjudicação segue a convenção acima. |
| “quero lifesteal” | A rubrica v1 não fixa um rótulo para a expressão isolada. Adotar SURVIVAL exige explicitar essa convenção em uma revisão futura, antes de novos dados/avaliações. |

A distinção é uma convenção de anotação, não uma propriedade inequívoca da linguagem natural.
Estes exemplos explicam o guia; não alteram regras congeladas, treino, validação ou candidatos.
