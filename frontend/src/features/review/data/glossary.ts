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
    definition: "O modelo tem poucos exemplos parecidos com esta situação; a comparação continua disponível, mas com menos confiança.",
  },
  {
    term: "Sem recomendação disponível",
    definition: "Não há evidência suficiente (ou o quadro de dados está desatualizado) para apontar qualquer alternativa; as opções continuam comparáveis por atributos.",
  },
];
