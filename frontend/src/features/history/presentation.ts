import type { MatchSummary } from "../../shared/api/client";
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

export function presentChampions(match: MatchSummary): string[] {
  return [...new Set((match.championsPresent ?? []).map((champion) =>
    champion.name ?? `Campeão #${champion.championId}`,
  ))];
}
