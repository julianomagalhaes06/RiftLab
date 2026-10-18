import type {
  DecisionSummary,
  MatchParticipant,
  MatchTimeline,
  TimelineItemEvent,
  TimelineObjective,
} from "../../shared/api/client";

export const BLUE = 100;
export const RED = 200;

export interface TeamTotals {
  team: number;
  kills: number | null;
  deaths: number | null;
  assists: number | null;
  win: boolean | null;
}

function sumOrNull(values: (number | null | undefined)[]): number | null {
  return values.every((value) => value != null) ? values.reduce<number>((sum, value) => sum + (value ?? 0), 0) : null;
}

export function teamTotals(participants: MatchParticipant[], team: number): TeamTotals {
  const members = participants.filter((p) => p.team === team);
  const wins = members.map((p) => p.win).filter((win): win is boolean => win != null);
  return {
    team,
    kills: members.length ? sumOrNull(members.map((p) => p.kills)) : null,
    deaths: members.length ? sumOrNull(members.map((p) => p.deaths)) : null,
    assists: members.length ? sumOrNull(members.map((p) => p.assists)) : null,
    win: wins.length ? wins[0] : null,
  };
}

export type ObjectiveKind = TimelineObjective["kind"];
export type ObjectiveCounts = Record<ObjectiveKind, number>;

export function objectiveCounts(objectives: TimelineObjective[], team: number): ObjectiveCounts {
  const counts: ObjectiveCounts = { DRAGON: 0, BARON: 0, HERALD: 0, VOIDGRUB: 0, ATAKHAN: 0, TOWER: 0, INHIBITOR: 0, OTHER: 0 };
  for (const objective of objectives) if (objective.team === team) counts[objective.kind] += 1;
  return counts;
}

export interface ShopVisit {
  startMs: number;
  items: TimelineItemEvent[];
}

/** Compras próximas (até `gapMs` entre uma e outra) são a mesma volta à loja. */
export function shopVisits(items: TimelineItemEvent[], gapMs = 45_000): ShopVisit[] {
  const visits: ShopVisit[] = [];
  for (const item of items) {
    const last = visits.at(-1);
    const previous = last?.items.at(-1);
    if (last && previous && item.timestampMs - previous.timestampMs <= gapMs) last.items.push(item);
    else visits.push({ startMs: item.timestampMs, items: [item] });
  }
  return visits;
}

const COMPLETED = new Set(["MAIN", "BOOTS"]);

export function isCompletedItem(item: Pick<TimelineItemEvent, "itemClass">): boolean {
  return item.itemClass != null && COMPLETED.has(item.itemClass);
}

/** Itens concluídos (principais e botas) que continuavam com o jogador: compra sem
 * venda posterior do mesmo item. É uma leitura da linha do tempo, não o inventário final. */
export function completedBuild(items: TimelineItemEvent[]): TimelineItemEvent[] {
  const kept: TimelineItemEvent[] = [];
  for (const item of items) {
    if (item.kind === "PURCHASE" && isCompletedItem(item)) kept.push(item);
    if (item.kind === "SOLD") {
      const index = kept.map((k) => k.itemId).lastIndexOf(item.itemId);
      if (index >= 0) kept.splice(index, 1);
    }
  }
  return kept.slice(-6);
}

export const SKILL_KEYS = ["Q", "W", "E", "R"] as const;

/** Slot (1..4) aprendido em cada nível, na ordem; só subidas normais contam. */
export function skillOrder(skills: MatchTimeline["participants"][number]["skills"]): number[] {
  return (skills ?? []).filter((skill) => skill.levelUpType === "NORMAL").map((skill) => skill.skillSlot).slice(0, 18);
}

/** Ordem de maximização das básicas: quem chega primeiro a 5 pontos; sem 5, mais pontos. */
export function maxOrder(order: number[]): number[] {
  const points: Record<number, number> = { 1: 0, 2: 0, 3: 0 };
  const maxedAt: Record<number, number> = {};
  order.forEach((slot, index) => {
    if (slot === 4) return;
    points[slot] += 1;
    if (points[slot] === 5) maxedAt[slot] = index;
  });
  return [1, 2, 3]
    .filter((slot) => points[slot] > 0)
    .sort((a, b) => (maxedAt[a] ?? 99) - (maxedAt[b] ?? 99) || points[b] - points[a]);
}

export interface GoldPoint {
  minute: number;
  blue: number;
  red: number;
  diff: number;
}

/** Diferença de ouro (azul − vermelho) por quadro; quadro sem todo o time fica de fora. */
export function goldDiffSeries(timeline: MatchTimeline, participants: MatchParticipant[]): GoldPoint[] {
  const teamOf = new Map(participants.map((p) => [p.participantId, p.team]));
  const points: GoldPoint[] = [];
  for (const frame of timeline.goldFrames ?? []) {
    let blue = 0;
    let red = 0;
    let complete = true;
    for (const participant of participants) {
      const gold = frame.gold[participant.participantId - 1];
      if (gold == null) {
        complete = false;
        break;
      }
      if (teamOf.get(participant.participantId) === BLUE) blue += gold;
      else red += gold;
    }
    if (complete) points.push({ minute: frame.timestampMs / 60_000, blue, red, diff: blue - red });
  }
  return points;
}

export interface ReasonSummary {
  total: number;
  eligible: number;
  /** Motivo → quantas decisões ele bloqueia, do mais frequente ao menos. */
  reasons: Array<[string, number]>;
}

export function summarizeDecisions(decisions: DecisionSummary[]): ReasonSummary {
  const counts = new Map<string, number>();
  for (const decision of decisions) {
    const suspended = decision.reconstructionRulesHealth === "SUSPENDED";
    if (decision.eligible && !suspended) continue;
    const codes = new Set(decision.reasonCodes ?? []);
    if (suspended) codes.add("INHERITED_RULES_SUSPENDED");
    for (const code of codes) counts.set(code, (counts.get(code) ?? 0) + 1);
  }
  return {
    total: decisions.length,
    eligible: decisions.filter((decision) => decision.eligible && decision.reconstructionRulesHealth !== "SUSPENDED").length,
    reasons: [...counts.entries()].sort((a, b) => b[1] - a[1]),
  };
}

/** Decisões cuja compra cai nesta visita à loja (o ponto tem o instante da compra). */
export function decisionsInVisit(visit: ShopVisit, decisions: DecisionSummary[]): DecisionSummary[] {
  const end = visit.items.at(-1)?.timestampMs ?? visit.startMs;
  return decisions.filter((d) => d.timestampMs >= visit.startMs && d.timestampMs <= end);
}

/** "+4,2 mil", "−800", "0". */
export function compactGold(value: number): string {
  const abs = Math.abs(value);
  const text = abs >= 1000 ? `${(abs / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} mil` : String(abs);
  return value > 0 ? `+${text}` : value < 0 ? `−${text}` : "0";
}
