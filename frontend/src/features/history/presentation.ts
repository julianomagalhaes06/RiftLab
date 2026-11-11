import type { MatchParticipant, MatchSummary } from "../../shared/api/client";
import { isDemoMatchId } from "../../shared/router/routes";

export function matchOrigin(match: MatchSummary): string {
  return isDemoMatchId(match.matchId) ? "DEMO · sintética" : "RIOT · importada";
}

export function matchDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? "Data não disponível"
    : date.toLocaleString("pt-BR", { dateStyle: "medium", timeStyle: "short" });
}

export function matchDuration(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** "36m 18s", como nos cartões de partida. */
export function matchLength(seconds: number): string {
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

export function presentChampions(match: MatchSummary): string[] {
  return [...new Set((match.championsPresent ?? []).map((champion) =>
    champion.name ?? `Campeão #${champion.championId}`,
  ))];
}

const QUEUE_TEXT: Readonly<Record<number, string>> = {
  420: "Ranqueada Solo/Duo",
  440: "Ranqueada Flex",
};

export function queueLabel(queue: number): string {
  return QUEUE_TEXT[queue] ?? `Fila ${queue}`;
}

const DAY_MS = 86_400_000;

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/** "há 5 min", "há 3 h", "ontem", "há 4 dias"; depois de uma semana, a data. */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Data não disponível";
  const minutes = Math.floor((now.getTime() - date.getTime()) / 60_000);
  if (minutes < 1) return "agora";
  if (minutes < 60) return `há ${minutes} min`;
  if (minutes < 24 * 60 && startOfDay(date) === startOfDay(now)) return `há ${Math.floor(minutes / 60)} h`;
  const days = Math.round((startOfDay(now) - startOfDay(date)) / DAY_MS);
  if (days <= 1) return "ontem";
  if (days < 7) return `há ${days} dias`;
  return date.toLocaleDateString("pt-BR", { day: "numeric", month: "short", year: "numeric" });
}

/** Linha de partida do Painel (CC-1203): "Hoje, 20:18", "Ontem, 22:04", "Há 3 dias"; depois de
 * uma semana, "14 de out." (com o ano se não for o atual). */
export function shortWhen(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Data não disponível";
  const days = Math.round((startOfDay(now) - startOfDay(date)) / DAY_MS);
  const time = date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  if (days <= 0) return `Hoje, ${time}`;
  if (days === 1) return `Ontem, ${time}`;
  if (days < 7) return `Há ${days} dias`;
  return date.toLocaleDateString("pt-BR", {
    day: "numeric",
    month: "short",
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
}

/** Rótulo do grupo diário: "Hoje", "Ontem" ou "qua., 14 de out.". */
export function dayLabel(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Data não disponível";
  const days = Math.round((startOfDay(now) - startOfDay(date)) / DAY_MS);
  if (days === 0) return "Hoje";
  if (days === 1) return "Ontem";
  return date.toLocaleDateString("pt-BR", {
    weekday: "short",
    day: "numeric",
    month: "short",
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
}

export function dayKey(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? "unknown"
    : `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

const ROLE_ORDER: Readonly<Record<string, number>> = { TOP: 0, JUNGLE: 1, MIDDLE: 2, BOTTOM: 3, UTILITY: 4 };

const ROLE_SHORT: Readonly<Record<string, string>> = {
  TOP: "Topo",
  JUNGLE: "Selva",
  MIDDLE: "Meio",
  BOTTOM: "Atirador",
  UTILITY: "Suporte",
};

export function roleShort(role: string | null | undefined): string | null {
  return role ? (ROLE_SHORT[role] ?? role) : null;
}

/** "Graves (Selva)": campeão e função curta, quando conhecida. */
export function championWithRole(name: string, role: string | null | undefined): string {
  const short = roleShort(role);
  return short ? `${name} (${short})` : name;
}

/** Os dois times em ordem de rota (topo → suporte); sem função, pela posição. */
export function teamsOf(match: MatchSummary): MatchParticipant[][] {
  const byTeam = new Map<number, MatchParticipant[]>();
  for (const participant of match.participants ?? []) {
    byTeam.set(participant.team, [...(byTeam.get(participant.team) ?? []), participant]);
  }
  const rank = (p: MatchParticipant) => (p.role && p.role in ROLE_ORDER ? ROLE_ORDER[p.role] : 9);
  return [...byTeam.keys()]
    .sort((a, b) => a - b)
    .map((team) =>
      (byTeam.get(team) ?? []).sort((a, b) => rank(a) - rank(b) || a.participantId - b.participantId),
    );
}

export interface LocalPerformance {
  participant: MatchParticipant;
  result: "WIN" | "LOSS" | null;
  /** (abates + assistências) ÷ mortes, mínimo 1; null sem placar completo. */
  kda: number | null;
  cs: number | null;
  csPerMinute: number | null;
  /** Participação em abates do time; null sem placar de todo o time. */
  killParticipation: number | null;
}

export function localParticipant(match: MatchSummary): MatchParticipant | null {
  if (match.localParticipantId == null) return null;
  return (match.participants ?? []).find((p) => p.participantId === match.localParticipantId) ?? null;
}

export function localPerformance(match: MatchSummary): LocalPerformance | null {
  const participant = localParticipant(match);
  if (!participant) return null;
  const { kills, deaths, assists } = participant;
  const scored = kills != null && deaths != null && assists != null;
  const cs =
    participant.totalMinionsKilled != null || participant.neutralMinionsKilled != null
      ? (participant.totalMinionsKilled ?? 0) + (participant.neutralMinionsKilled ?? 0)
      : null;
  const team = (match.participants ?? []).filter((p) => p.team === participant.team);
  const teamKills = team.every((p) => p.kills != null)
    ? team.reduce((sum, p) => sum + (p.kills ?? 0), 0)
    : null;
  return {
    participant,
    result: participant.win == null ? null : participant.win ? "WIN" : "LOSS",
    kda: scored ? (kills + assists) / Math.max(1, deaths) : null,
    cs,
    csPerMinute: cs != null && match.durationSeconds > 0 ? cs / (match.durationSeconds / 60) : null,
    killParticipation:
      scored && teamKills ? Math.min(1, (kills + assists) / teamKills) : null,
  };
}

export interface RecordSummary {
  games: number;
  wins: number;
  losses: number;
}

export function record(matches: MatchSummary[]): RecordSummary {
  let wins = 0;
  let losses = 0;
  for (const match of matches) {
    const result = localPerformance(match)?.result;
    if (result === "WIN") wins += 1;
    if (result === "LOSS") losses += 1;
  }
  return { games: wins + losses, wins, losses };
}

export interface ChampionRecord extends RecordSummary {
  participant: MatchParticipant;
  /** KDA de somas nas partidas desse campeão com placar completo. */
  kda: number | null;
}

export interface RoleRecord extends RecordSummary {
  role: string;
}

export interface LoadedSummary extends RecordSummary {
  /** KDA de somas (não a média dos quocientes), como no Painel. */
  kda: number | null;
  averages: { kills: number; deaths: number; assists: number } | null;
  champions: ChampionRecord[];
  /** Resultados da conta, da partida mais nova para a mais antiga (até 20). */
  recent: Array<"WIN" | "LOSS">;
  /** Rotas jogadas pela conta, da mais frequente para a menos. */
  roles: RoleRecord[];
}

/** Resumo só das partidas carregadas em que a conta local foi identificada. */
export function summarizeLoaded(matches: MatchSummary[]): LoadedSummary {
  const performances = matches.map(localPerformance).filter((p): p is LocalPerformance => p !== null);
  const scored = performances.filter((p) => p.kda !== null).map((p) => p.participant);
  const totals = scored.reduce(
    (sum, p) => ({
      kills: sum.kills + (p.kills ?? 0),
      deaths: sum.deaths + (p.deaths ?? 0),
      assists: sum.assists + (p.assists ?? 0),
    }),
    { kills: 0, deaths: 0, assists: 0 },
  );
  const byChampion = new Map<number, ChampionRecord & { kills: number; deaths: number; assists: number; scored: number }>();
  const byRole = new Map<string, RoleRecord>();
  for (const performance of performances) {
    const { participant } = performance;
    const entry = byChampion.get(participant.championId)
      ?? { participant, games: 0, wins: 0, losses: 0, kda: null, kills: 0, deaths: 0, assists: 0, scored: 0 };
    entry.games += 1;
    if (performance.result === "WIN") entry.wins += 1;
    if (performance.result === "LOSS") entry.losses += 1;
    if (performance.kda !== null) {
      entry.kills += participant.kills ?? 0;
      entry.deaths += participant.deaths ?? 0;
      entry.assists += participant.assists ?? 0;
      entry.scored += 1;
      entry.kda = (entry.kills + entry.assists) / Math.max(1, entry.deaths);
    }
    byChampion.set(participant.championId, entry);
    if (participant.role) {
      const role = byRole.get(participant.role) ?? { role: participant.role, games: 0, wins: 0, losses: 0 };
      role.games += 1;
      if (performance.result === "WIN") role.wins += 1;
      if (performance.result === "LOSS") role.losses += 1;
      byRole.set(participant.role, role);
    }
  }
  const { wins, losses } = record(matches);
  return {
    games: performances.length,
    wins,
    losses,
    kda: scored.length ? (totals.kills + totals.assists) / Math.max(1, totals.deaths) : null,
    averages: scored.length
      ? {
          kills: totals.kills / scored.length,
          deaths: totals.deaths / scored.length,
          assists: totals.assists / scored.length,
        }
      : null,
    champions: [...byChampion.values()]
      .sort((a, b) => b.games - a.games || b.wins - a.wins)
      .slice(0, 5)
      .map(({ participant, games, wins: w, losses: l, kda }) => ({ participant, games, wins: w, losses: l, kda })),
    recent: performances.flatMap((p) => (p.result ? [p.result] : [])).slice(0, 20),
    roles: [...byRole.values()].sort((a, b) => b.games - a.games),
  };
}

export function decimal(value: number, digits = 1): string {
  return value.toLocaleString("pt-BR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

/** Vitórias seguidas a partir da partida mais nova. */
export function winStreak(recent: Array<"WIN" | "LOSS">): number {
  const firstLoss = recent.indexOf("LOSS");
  return firstLoss === -1 ? recent.length : firstLoss;
}

/** Destaques das partidas carregadas (só dado real), como no tracker.gg e no u.gg: o próprio
 * número vira o destaque (cor dourada e estrela), sem etiqueta extra. Só com ao menos três
 * partidas da conta. */
export type HighlightKind = "kda" | "cs" | "kp";

export const HIGHLIGHT_TEXT: Record<HighlightKind, string> = {
  kda: "Maior KDA das partidas carregadas",
  cs: "Mais farm por minuto das partidas carregadas",
  kp: "Maior participação em abates das partidas carregadas",
};

export function loadedHighlights(matches: MatchSummary[]): Record<string, HighlightKind[]> {
  const performances = matches.flatMap((match) => {
    const performance = localPerformance(match);
    return performance ? [{ matchId: match.matchId, performance }] : [];
  });
  if (performances.length < 3) return {};
  const result: Record<string, HighlightKind[]> = {};
  const mark = (kind: HighlightKind, pick: (p: (typeof performances)[number]["performance"]) => number | null) => {
    let best: { matchId: string; value: number } | null = null;
    for (const { matchId, performance } of performances) {
      const value = pick(performance);
      if (value !== null && (best === null || value > best.value)) best = { matchId, value };
    }
    if (best) (result[best.matchId] ??= []).push(kind);
  };
  mark("kda", (p) => p.kda);
  mark("cs", (p) => p.csPerMinute);
  mark("kp", (p) => p.killParticipation);
  return result;
}
