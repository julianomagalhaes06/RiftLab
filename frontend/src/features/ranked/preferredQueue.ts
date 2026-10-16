import type { RankedMe, RankedQueue } from "../../shared/api/client";

const tiers = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"];
const divisions = ["IV", "III", "II", "I"];

function score(data?: RankedMe): number[] {
  const position = data?.position;
  if (position?.state === "UNRANKED") return [1];
  if (position?.state !== "READY" || !position.tier || !tiers.includes(position.tier)) return [0];
  return [2, data?.status === "READY" ? 1 : 0, tiers.indexOf(position.tier), divisions.indexOf(position.division ?? ""), position.leaguePoints ?? 0];
}

export function preferredQueue(solo?: RankedMe, flex?: RankedMe): RankedQueue {
  const soloScore = score(solo);
  const flexScore = score(flex);
  for (let index = 0; index < Math.max(soloScore.length, flexScore.length); index += 1) {
    const difference = (flexScore[index] ?? 0) - (soloScore[index] ?? 0);
    if (difference !== 0) return difference > 0 ? "RANKED_FLEX_SR" : "RANKED_SOLO_5x5";
  }
  return "RANKED_SOLO_5x5";
}
