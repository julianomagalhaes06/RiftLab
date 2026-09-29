import type {
  ChampionAsset,
  BudgetSource,
  DecisionBudget,
  InventoryItem,
  MatchParticipant,
  PreparationStatus,
} from "../../shared/api/client";

/** mm:ss do tempo de jogo (ex.: 472000 ms → "7:52"). */
export function formatGameTime(timestampMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(timestampMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/** "há 20 s", "há 7 min 52 s" — idade de um quadro de referência. */
export function formatAge(ageMs: number): string {
  const totalSeconds = Math.max(0, Math.round(ageMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes === 0) {
    return `há ${seconds} s`;
  }
  return seconds === 0 ? `há ${minutes} min` : `há ${minutes} min ${seconds} s`;
}

export type BudgetTone = "approximate" | "stale" | "unknown" | "provided";

export interface BudgetDescription {
  tone: BudgetTone;
  /** Texto principal, sempre presente. */
  headline: string;
  /** Explicações adicionais (uma frase cada). */
  notes: string[];
}

function goldText(value: number): string {
  return `${value.toLocaleString("pt-BR")} de ouro`;
}

/**
 * Descreve o orçamento imediato sem nunca inventar valor: valor ausente é
 * "desconhecido" (NUNCA 0); quadro antigo é "snapshot antigo; orçamento não
 * confirmável"; estimativa de quadro é "aproximado", com a idade do quadro.
 */
export function describeBudget(
  budget: Pick<
    DecisionBudget,
    "value" | "source" | "frameAgeMs" | "staleFrame" | "immediatePurchaseAssertable"
  >,
): BudgetDescription {
  const notes: string[] = [];
  const value = typeof budget.value === "number" ? budget.value : null;
  const age = typeof budget.frameAgeMs === "number" ? formatAge(budget.frameAgeMs) : null;

  if (budget.source === "USER_PROVIDED" && value !== null) {
    return { tone: "provided", headline: `Informado por você: ${goldText(value)}`, notes };
  }

  let description: BudgetDescription;
  if (budget.staleFrame) {
    notes.push(
      age
        ? `O último quadro observado é ${age}.`
        : "Não há um quadro recente para confirmar o valor.",
    );
    if (value !== null) {
      notes.push(`Valor desse quadro antigo: ${goldText(value)} (não confirmável).`);
    }
    description = {
      tone: "stale",
      headline: "Snapshot antigo; orçamento não confirmável",
      notes,
    };
  } else if (budget.source === "FRAME_ESTIMATE" && value !== null) {
    if (age) {
      notes.push(`Estimado a partir do quadro observado ${age}.`);
    }
    description = {
      tone: "approximate",
      headline: `Aproximadamente ${goldText(value)}`,
      notes,
    };
  } else {
    description = { tone: "unknown", headline: "Orçamento desconhecido", notes };
  }
  if (!budget.immediatePurchaseAssertable) {
    description.notes.push("Não é possível afirmar que a compra imediata era possível.");
  }
  return description;
}

/** Versão curta para a lista de decisões (sem valor). */
export function describeBudgetShort(
  source: BudgetSource,
  frameAgeMs: number | null | undefined,
  staleFrame: boolean,
): { tone: BudgetTone; text: string } {
  if (staleFrame) {
    return { tone: "stale", text: "Snapshot antigo; orçamento não confirmável" };
  }
  if (source === "FRAME_ESTIMATE") {
    const age = typeof frameAgeMs === "number" ? ` (quadro ${formatAge(frameAgeMs)})` : "";
    return { tone: "approximate", text: `Orçamento aproximado${age}` };
  }
  if (source === "USER_PROVIDED") {
    return { tone: "provided", text: "Orçamento informado por você" };
  }
  return { tone: "unknown", text: "Orçamento desconhecido" };
}

const REASON_TEXT: Readonly<Record<string, string>> = {
  MISSING_CATALOG: "Catálogo de itens indisponível para o patch desta partida.",
  OUT_OF_SCOPE:
    "Fora do recorte suportado pelo RiftLab (fila, mapa, patch, função, campeão ou posição do item).",
  INVENTORY_UNCERTAIN:
    "O inventário anterior à decisão é incerto: não dá para reconstruí-lo com segurança.",
  // Neutro de propósito: não diz o motivo específico do alvo (modo Estudo).
  TARGET_NOT_ELIGIBLE: "Esta decisão não é elegível para análise.",
};

const SUB_REASON_TEXT: Readonly<Record<string, string>> = {
  QUEUE_OUT_OF_SCOPE: "fila fora do recorte",
  MAP_OUT_OF_SCOPE: "mapa fora do recorte",
  PATCH_OUT_OF_SCOPE: "patch fora do recorte",
  ROLE_UNKNOWN: "função do jogador desconhecida",
  ROLE_OUT_OF_SCOPE: "função fora do recorte",
  CHAMPION_OUT_OF_SCOPE: "campeão fora do recorte",
  MAIN_ITEM_ORDINAL_BEYOND_SECOND: "já havia dois ou mais itens principais",
  NO_CATALOG: "sem catálogo para o patch",
  ITEM_UNKNOWN: "item desconhecido no catálogo",
  OWNED_ITEM_UNCLASSIFIABLE: "item já possuído sem classificação",
  UNVALIDATED_UNDO_SEMANTICS: "desfazer compra ainda não validado",
  UNVALIDATED_DESTROY_RECONCILIATION: "destruição de item ainda não validada",
  UNVALIDATED_DESTROY_ORDER_RECONCILIATION: "ordem de destruição e compra ainda não validada",
  UNRECONCILED_ITEM_DESTROYED: "item destruído sem correspondência",
  INVENTORY_CAPACITY_EXCEEDED: "capacidade do inventário excedida",
  SALE_WITHOUT_OWNERSHIP: "venda de item não possuído",
  UNDO_UNCORRELATED: "desfazer sem correlação",
};

export function reasonText(code: string): string {
  return REASON_TEXT[code] ?? `Motivo não classificado (${code}).`;
}

export function subReasonText(code: string): string {
  return SUB_REASON_TEXT[code] ?? code;
}

const ROLE_TEXT: Readonly<Record<string, string>> = {
  TOP: "Topo",
  JUNGLE: "Selva",
  MIDDLE: "Meio",
  BOTTOM: "Atirador (bot)",
  UTILITY: "Suporte",
};

export function roleLabel(role: string | null | undefined): string {
  if (!role) {
    return "Função não informada";
  }
  return ROLE_TEXT[role] ?? `Função ${role}`;
}

export function championLabel(championId: number): string {
  return `Campeão #${championId}`;
}

/** Host fixo do CDN oficial (CC-208). Nenhuma outra origem de imagem é montada. */
export const DATA_DRAGON_HOST = "https://ddragon.leagueoflegends.com";
const VERSION_PATTERN = /^\d{1,3}\.\d{1,3}\.\d{1,3}$/;
const IMAGE_FILE_PATTERN = /^[A-Za-z0-9]{1,64}\.png$/;

/** URL do retrato quadrado, ou null se versão/arquivo não passarem na validação
 * (defesa em profundidade: a API já valida, mas a interface nunca monta caminho livre). */
export function championPortraitUrl(
  champion: Pick<ChampionAsset, "dataDragonVersion" | "imageFile"> | null | undefined,
): string | null {
  if (
    !champion ||
    !VERSION_PATTERN.test(champion.dataDragonVersion) ||
    !IMAGE_FILE_PATTERN.test(champion.imageFile)
  ) {
    return null;
  }
  return `${DATA_DRAGON_HOST}/cdn/${champion.dataDragonVersion}/img/champion/${champion.imageFile}`;
}

/** Nome do campeão quando resolvido; senão o rótulo numérico honesto. */
export function championName(participant: Pick<MatchParticipant, "championId" | "champion">): string {
  return participant.champion?.name ?? championLabel(participant.championId);
}

export function teamLabel(team: number): string {
  return `Time ${team}`;
}

export function participantLabel(participant: MatchParticipant): string {
  return `Participante ${participant.participantId} — ${championName(participant)}`;
}

export function itemLabel(item: Pick<InventoryItem, "itemId" | "name">): string {
  return item.name ? item.name : `Item #${item.itemId}, nome indisponível`;
}

export function trinketLabel(trinketItemId: number | null | undefined): string {
  return typeof trinketItemId === "number" ? `Item #${trinketItemId}` : "Vazio";
}

export function ordinalLabel(ordinal: number | null | undefined): string | null {
  return typeof ordinal === "number" ? `${ordinal}º item principal` : null;
}

export const PREPARATION_LABEL: Readonly<Record<PreparationStatus, string>> = {
  NOT_REQUESTED: "Não preparada",
  PENDING: "Aguardando preparação",
  RUNNING: "Preparando",
  READY: "Pronta",
  FAILED: "Preparação falhou",
};
