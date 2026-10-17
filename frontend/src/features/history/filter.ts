import type { MatchSummary } from "../../shared/api/client";
import { isDemoMatchId } from "../../shared/router/routes";
import { localPerformance } from "./presentation";

export type OriginFilter = "ALL" | "RIOT" | "DEMO";
export type ResultFilter = "ALL" | "WIN" | "LOSS";
export type QueueFilter = "ALL" | 420 | 440;

/** Busca apenas nas páginas já carregadas. Campeão = qualquer participante.
 * O resultado só existe nas partidas em que a conta local foi identificada. */
export function filterLoadedMatches(
  matches: MatchSummary[],
  query: string,
  origin: OriginFilter,
  result: ResultFilter = "ALL",
  queue: QueueFilter = "ALL",
): MatchSummary[] {
  const term = query.trim().toLocaleLowerCase("pt-BR");
  return matches.filter((match) => {
    const demo = isDemoMatchId(match.matchId);
    if ((origin === "DEMO" && !demo) || (origin === "RIOT" && demo)) return false;
    if (queue !== "ALL" && match.queue !== queue) return false;
    if (result !== "ALL" && localPerformance(match)?.result !== result) return false;
    if (!term) return true;
    return (
      match.matchId.toLocaleLowerCase("pt-BR").includes(term) ||
      (match.championsPresent ?? []).some((champion) =>
        (champion.name ?? `Campeão #${champion.championId}`)
          .toLocaleLowerCase("pt-BR")
          .includes(term),
      )
    );
  });
}
