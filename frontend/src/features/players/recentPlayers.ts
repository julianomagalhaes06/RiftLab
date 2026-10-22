import type { PlayerProfile } from "../../shared/api/client";
import { parseRiotId } from "../../shared/router/routes";
import { betterRank, isTier, type Tier } from "../ranked/tiers";

/** Buscas recentes de jogador (CC-1208), como as do OP.GG: ficam só neste navegador
 * (localStorage), nunca no servidor. O banco guarda só HMACs de terceiros (CC-1103); a API da
 * Riot não busca por parte do nome, então a sugestão vem dos perfis que a própria pessoa abriu. */
export type RecentPlayer = {
  gameName: string;
  tagLine: string;
  profileIconId: number | null;
  summonerLevel: number | null;
  dataDragonVersion: string | null;
  rank: { tier: Tier; division: string | null; leaguePoints: number } | null;
  viewedAt: string;
};

const KEY = "riftlab:recent-players:v1";
const LIMIT = 8;
const SHOWN = 5;
/** TAG sugerida quando o texto não tem "#": a mais comum no servidor BR. */
export const DEFAULT_TAG = "BR1";

const keyOf = (player: { gameName: string; tagLine: string }) =>
  `${player.gameName}#${player.tagLine}`.toLocaleLowerCase("pt-BR");

const intOrNull = (value: unknown) => (typeof value === "number" && Number.isInteger(value) ? value : null);

/** Entrada lida do armazenamento: qualquer coisa fora do formato é descartada. */
function sanitize(value: unknown): RecentPlayer | null {
  if (!value || typeof value !== "object") return null;
  const entry = value as Record<string, unknown>;
  if (typeof entry.gameName !== "string" || typeof entry.tagLine !== "string") return null;
  if (!parseRiotId(`${entry.gameName}#${entry.tagLine}`)) return null;
  const rank = entry.rank as Record<string, unknown> | null | undefined;
  return {
    gameName: entry.gameName,
    tagLine: entry.tagLine,
    profileIconId: intOrNull(entry.profileIconId),
    summonerLevel: intOrNull(entry.summonerLevel),
    dataDragonVersion: typeof entry.dataDragonVersion === "string" ? entry.dataDragonVersion : null,
    rank: rank && typeof rank === "object" && isTier(rank.tier as string)
      ? { tier: rank.tier as Tier, division: typeof rank.division === "string" ? rank.division : null, leaguePoints: intOrNull(rank.leaguePoints) ?? 0 }
      : null,
    viewedAt: typeof entry.viewedAt === "string" ? entry.viewedAt : "",
  };
}

export function readRecentPlayers(): RecentPlayer[] {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(KEY) ?? "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed.map(sanitize).filter((entry): entry is RecentPlayer => entry !== null).slice(0, LIMIT);
  } catch {
    return []; // armazenamento bloqueado ou corrompido: a busca funciona sem sugestões
  }
}

function write(entries: RecentPlayer[]): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(entries));
  } catch {
    // navegador sem armazenamento (janela privada, bloqueio): segue sem lembrar
  }
}

/** Guarda o perfil aberto no topo da lista, sem repetir o mesmo Riot ID. */
export function rememberPlayer(profile: PlayerProfile, now: Date = new Date()): void {
  const best = betterRank(profile.solo, profile.flex);
  const entry: RecentPlayer = {
    gameName: profile.gameName,
    tagLine: profile.tagLine,
    profileIconId: profile.profileIconId ?? null,
    summonerLevel: profile.summonerLevel ?? null,
    dataDragonVersion: profile.dataDragonVersion ?? null,
    rank: best?.state === "READY" && isTier(best.tier)
      ? { tier: best.tier, division: best.division ?? null, leaguePoints: best.leaguePoints ?? 0 }
      : null,
    viewedAt: now.toISOString(),
  };
  const others = readRecentPlayers().filter((other) => keyOf(other) !== keyOf(entry));
  write([entry, ...others].slice(0, LIMIT));
}

export function clearRecentPlayers(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    // nada guardado ou armazenamento indisponível
  }
}

/** Perfis recentes cujo "Nome#TAG" contém o texto; sem texto, os mais recentes. */
export function matchRecentPlayers(query: string, recent: RecentPlayer[]): RecentPlayer[] {
  const text = query.trim().toLocaleLowerCase("pt-BR");
  return (text ? recent.filter((player) => keyOf(player).includes(text)) : recent).slice(0, SHOWN);
}

/** "Nome" sem "#" vira "Nome#BR1", se o nome for válido; com "#", nenhuma sugestão. */
export function tagSuggestion(query: string): { gameName: string; tagLine: string } | null {
  const text = query.trim();
  if (!text || text.includes("#")) return null;
  return parseRiotId(`${text}#${DEFAULT_TAG}`);
}
