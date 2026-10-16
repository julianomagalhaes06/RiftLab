import type { GlossaryEntry } from "../types";

export const GLOSSARY: GlossaryEntry[] = [
  {
    term: "Composição",
    definition: "Conjunto de funções/campeões do seu time e do time adversário conhecido até o momento da decisão.",
  },
  {
    term: "Componente",
    definition: "Item intermediário que faz parte da receita de um item principal maior.",
  },
  {
    term: "Item principal",
    definition: "Item completo classificado como principal pelo catálogo e pelas regras do patch. Componentes, botas, consumíveis e trinkets têm categorias próprias.",
  },
  {
    term: "Custo restante",
    definition: "Quanto falta pagar para completar um item principal, descontando componentes já possuídos.",
  },
  {
    term: "Meta de item completo",
    definition: "Próximo item principal completo visado, não necessariamente uma compra acessível agora.",
  },
  {
    term: "Compra imediata (hipotética)",
    definition: "Simulação de compra no orçamento informado manualmente; não é dado real de treino nem promessa de disponibilidade.",
  },
  {
    term: "Ouro estimado",
    definition: "Aproximação a partir do último quadro de dados capturado antes da decisão; mostra a idade da estimativa e nunca é saldo exato.",
  },
  {
    term: "Ouro desconhecido",
    definition: "Ausência de orçamento confiável; a comparação segue por meta de item, sem afirmar que a compra é possível agora.",
  },
  {
    term: "Suporte limitado",
    definition: "O recorte possui limitações de dados ou de validação, indicadas na tela. Esse aviso não é uma probabilidade de acerto nem garante que um item seja melhor.",
  },
  {
    term: "Sem recomendação disponível",
    definition: "O sistema se abstém de ordenar alternativas quando faltam dados, regras ou suporte necessários. Consulte o motivo exibido; informação ausente não significa valor zero.",
  },
  {
    term: "Elegibilidade",
    definition: "Verificação de que a decisão atende ao recorte e aos requisitos de reconstrução. Ser elegível não significa que a compra histórica foi uma boa escolha.",
  },
  {
    term: "Popularidade",
    definition: "Critério de referência que usa a frequência de compras no treino, por campeão e etapa da build. Uma compra frequente não é necessariamente a mais adequada ao contexto.",
  },
  {
    term: "Objetivo",
    definition: "Prioridade escolhida para a comparação: equilíbrio, dano, sobrevivência ou utilidade. Ela reordena alternativas legais, sem mudar o preço ou tornar um item permitido.",
  },
  {
    term: "Abstenção",
    definition: "Decisão de não recomendar quando os requisitos de análise não são atendidos. Pedir mais informação ou manter a análise indisponível é preferível a inventar certeza.",
  },
];
