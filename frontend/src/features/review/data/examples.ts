import type { DecisionExample, ParticipantExample, TransferCase } from "../types";

/**
 * Dados de apresentação ilustrativos e tipados, isolados do frontend. Não são
 * catálogo validado, recomendação legal nem fixture do pipeline (CC-104/CC-105).
 * IDs, preços e atributos são inventados de propósito para não parecerem dados
 * reais de League of Legends; função/campeões/patch do recorte real ficam para
 * CC-105.
 */

const ALT_DANO: DecisionExample["alternatives"][number] = {
  id: "alt-dano",
  label: "Alternativa A",
  tags: ["dano", "área"],
  remainingCost: 1400,
  postRevealNote:
    "Prioriza dano em área; compromisso: pouca sobrevivência contra investidas.",
};

const ALT_SOBREVIVENCIA: DecisionExample["alternatives"][number] = {
  id: "alt-sobrevivencia",
  label: "Alternativa B",
  tags: ["sobrevivência", "resistência"],
  remainingCost: 2100,
  postRevealNote:
    "Prioriza sobrevivência; compromisso: menor contribuição ofensiva imediata.",
};

const ALT_UTILIDADE: DecisionExample["alternatives"][number] = {
  id: "alt-utilidade",
  label: "Alternativa C",
  tags: ["utilidade", "controle"],
  remainingCost: 1800,
  postRevealNote:
    "Prioriza utilidade para o time; compromisso: impacto individual mais lento.",
};

export const EXAMPLE_DECISIONS: DecisionExample[] = [
  {
    id: "dec-normal-disponivel",
    kind: "normal",
    label: "Meta de item após primeiro recuo (suporte disponível)",
    timeLabel: "14:32 (ilustrativo)",
    participantRole: "Função de exemplo",
    levelLabel: "Nível 9 (ilustrativo)",
    compositionNote:
      "Composição própria com foco misto; composição adversária com ameaça de dano físico observável.",
    ownState: "Vida e recursos parciais no momento da decisão; sem dado de resultado final.",
    inventorySummary: ["1 componente de dano (ilustrativo)", "1 item de bota básica (ilustrativo)"],
    budget: { source: "FRAME_ESTIMATE", approxGold: 1750, ageSeconds: 12, stale: false },
    analysisStatus: "AVAILABLE",
    supportLevel: "FULL",
    alternatives: [ALT_DANO, ALT_SOBREVIVENCIA, ALT_UTILIDADE],
  },
  {
    id: "dec-normal-limitado",
    kind: "normal",
    label: "Meta de item em rota isolada (suporte limitado)",
    timeLabel: "9:05 (ilustrativo)",
    participantRole: "Função de exemplo",
    levelLabel: "Nível 6 (ilustrativo)",
    compositionNote:
      "Poucas partidas semelhantes nesta combinação de função e fase de jogo no contexto de treino ilustrativo.",
    ownState: "Recursos desconhecidos; nenhum orçamento confiável informado.",
    inventorySummary: ["Nenhum componente conhecido"],
    budget: { source: "UNKNOWN" },
    analysisStatus: "AVAILABLE",
    supportLevel: "LIMITED",
    reasonNote: "SMALL_CONTEXT_SAMPLE (ilustrativo): poucos exemplos semelhantes no contexto de treino.",
    alternatives: [ALT_SOBREVIVENCIA, ALT_UTILIDADE],
  },
  {
    id: "dec-normal-sem-recomendacao",
    kind: "normal",
    label: "Meta de item com quadro defasado (sem recomendação)",
    timeLabel: "21:47 (ilustrativo)",
    participantRole: "Função de exemplo",
    levelLabel: "Nível 13 (ilustrativo)",
    compositionNote: "Composições registradas normalmente; decisão tardia na partida.",
    ownState:
      "Último quadro de ouro capturado há mais de 90s; considerado desatualizado (STALE_FRAME, ilustrativo).",
    inventorySummary: ["2 componentes de utilidade (ilustrativo)"],
    budget: { source: "FRAME_ESTIMATE", approxGold: 4200, ageSeconds: 96, stale: true },
    analysisStatus: "UNSUPPORTED",
    supportLevel: "LIMITED",
    reasonNote:
      "STALE_FRAME (ilustrativo): quadro de ouro com mais de 90s; nenhuma alternativa é apresentada como recomendada.",
    alternatives: [ALT_DANO, ALT_SOBREVIVENCIA, ALT_UTILIDADE],
  },
  {
    id: "dec-incompativel",
    kind: "incompatible",
    label: "Decisão de patch fora do recorte suportado",
    timeLabel: "—",
    participantRole: "—",
    levelLabel: "—",
    compositionNote: "—",
    ownState: "—",
    inventorySummary: [],
    budget: { source: "UNKNOWN" },
    analysisStatus: "UNSUPPORTED",
    supportLevel: "LIMITED",
    alternatives: [],
    incompatibleReason:
      "Este cenário ilustra um patch fora do recorte inicial suportado (OUT_OF_SCOPE). Nenhuma análise é produzida; volte e escolha outra decisão.",
  },
  {
    id: "dec-catalogo-ausente",
    kind: "missing-catalog",
    label: "Decisão com inventário incerto",
    timeLabel: "—",
    participantRole: "—",
    levelLabel: "—",
    compositionNote: "—",
    ownState: "—",
    inventorySummary: [],
    budget: { source: "UNKNOWN" },
    analysisStatus: "UNSUPPORTED",
    supportLevel: "LIMITED",
    alternatives: [],
    missingCatalogReason:
      "Este cenário ilustra um evento de inventário não correlacionável com segurança (INVENTORY_UNCERTAIN, ilustrativo). O sistema se abstém em vez de supor; volte e escolha outra decisão.",
  },
];

export const EXAMPLE_PARTICIPANTS: ParticipantExample[] = [
  {
    id: "participante-a",
    label: "Participante A — decisões disponíveis",
    decisions: EXAMPLE_DECISIONS,
  },
  {
    id: "participante-b",
    label: "Participante B — sem decisões elegíveis",
    decisions: [],
    emptyReason:
      "Nenhuma compra de item principal completo foi identificada para este participante nesta partida ilustrativa.",
  },
];

export const TRANSFER_CASES: TransferCase[] = [
  {
    id: "transfer-1",
    label: "Cenário de transferência 1 (independente)",
    timeLabel: "11:20 (ilustrativo)",
    compositionNote: "Composição adversária com ameaça de controle de área.",
    ownState: "Recursos parciais; sem histórico de compra nesta tentativa.",
    alternatives: [ALT_DANO, ALT_SOBREVIVENCIA, ALT_UTILIDADE],
  },
  {
    id: "transfer-2",
    label: "Cenário de transferência 2 (independente)",
    timeLabel: "17:58 (ilustrativo)",
    compositionNote: "Composição própria com pouca sobrevivência declarada.",
    ownState: "Recursos parciais; sem histórico de compra nesta tentativa.",
    alternatives: [ALT_SOBREVIVENCIA, ALT_UTILIDADE],
  },
];
