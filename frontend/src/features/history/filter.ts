import type { MatchSummary } from "../../shared/api/client";
import { isDemoMatchId } from "../../shared/router/routes";

export type OriginFilter = "ALL" | "RIOT" | "DEMO";

/** Busca apenas nas páginas já carregadas. Campeão = qualquer participante. */
export function filterLoadedMatches(
  matches: MatchSummary[],
  query: string,
  origin: OriginFilter,
): MatchSummary[] {
  const term = query.trim().toLocaleLowerCase("pt-BR");
  return matches.filter((match) => {
    const demo = isDemoMatchId(match.matchId);
    if ((origin === "DEMO" && !demo) || (origin === "RIOT" && demo)) return false;
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
