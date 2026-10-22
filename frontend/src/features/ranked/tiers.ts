/** Elos, divisões e escada do ranqueado (CC-1202): uma fonte para o chip do topo, o card de elo
 * e a página do jogador. Até Diamante, quatro divisões de 0 a 100 PDL; Mestre, Grão-Mestre e
 * Desafiante sem divisões e com PDL sem teto. */

export const TIER_ORDER = [
  "IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER",
] as const;
export type Tier = (typeof TIER_ORDER)[number];

export const TIER_NAMES: Record<Tier, string> = {
  IRON: "Ferro", BRONZE: "Bronze", SILVER: "Prata", GOLD: "Ouro",
  PLATINUM: "Platina", EMERALD: "Esmeralda", DIAMOND: "Diamante",
  MASTER: "Mestre", GRANDMASTER: "Grão-Mestre", CHALLENGER: "Desafiante",
};

export const DIVISIONS = ["IV", "III", "II", "I"] as const;
const APEX: ReadonlySet<string> = new Set(["MASTER", "GRANDMASTER", "CHALLENGER"]);

export function isTier(value: string | null | undefined): value is Tier {
  return value != null && (TIER_ORDER as readonly string[]).includes(value);
}

export function isApex(tier: Tier): boolean {
  return APEX.has(tier);
}

/** "Ouro III"; elos do topo sem divisão. */
export function rankTitle(tier: Tier, division: string | null | undefined): string {
  return isApex(tier) || !division ? TIER_NAMES[tier] : `${TIER_NAMES[tier]} ${division}`;
}

/** Próximo degrau: IV → III → II → I → IV do elo seguinte; Diamante I → Mestre. */
export function nextStep(tier: Tier, division: string | null | undefined): { tier: Tier; division: string | null } | null {
  const index = DIVISIONS.indexOf((division ?? "") as (typeof DIVISIONS)[number]);
  if (isApex(tier) || index < 0) return null;
  if (index < DIVISIONS.length - 1) return { tier, division: DIVISIONS[index + 1] };
  const next = TIER_ORDER[TIER_ORDER.indexOf(tier) + 1];
  return { tier: next, division: isApex(next) ? null : "IV" };
}

/** Caminho do emblema oficial (pacote Ranked Emblems) em public/. */
export function emblemSrc(tier: Tier): string {
  return `/ranked-emblems/${tier.toLowerCase()}.webp`;
}

type RankLike = { state: string; tier?: string | null; division?: string | null; leaguePoints?: number | null };

/** Ordem para escolher a melhor fila: elo, divisão e PDL; classificada antes de sem elo. */
export function rankScore(rank: RankLike | null | undefined): number[] {
  if (!rank) return [0];
  if (rank.state !== "READY" || !isTier(rank.tier)) return [1];
  return [2, TIER_ORDER.indexOf(rank.tier), DIVISIONS.indexOf((rank.division ?? "") as (typeof DIVISIONS)[number]), rank.leaguePoints ?? 0];
}

export function betterRank<T extends RankLike>(first: T | null | undefined, second: T | null | undefined): T | null {
  const a = rankScore(first);
  const b = rankScore(second);
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    const difference = (b[index] ?? 0) - (a[index] ?? 0);
    if (difference !== 0) return (difference > 0 ? second : first) ?? null;
  }
  return first ?? second ?? null;
}

/** Ícone de invocador do Data Dragon na versão do catálogo. */
export function profileIconUrl(profile: { profileIconId?: number | null; dataDragonVersion?: string | null }): string | null {
  if (profile.profileIconId == null || !profile.dataDragonVersion) return null;
  return `https://ddragon.leagueoflegends.com/cdn/${profile.dataDragonVersion}/img/profileicon/${profile.profileIconId}.png`;
}
