import { describe, expect, it } from "vitest";
import type { RankedMe, RankedQueue } from "../../shared/api/client";
import { preferredQueue } from "./preferredQueue";

function observation(queue: RankedQueue, tier = "GOLD", division: string | null = "IV", leaguePoints = 20): RankedMe {
  return { status: "READY", reason: null, change: null, position: {
    queue, state: "READY", season: "2026", observedAt: "2026-10-16T12:00:00Z", origin: "RIOT_LEAGUE_V4",
    tier, division, leaguePoints, wins: 10, losses: 5,
  } };
}

const solo = observation("RANKED_SOLO_5x5");
const flex = observation("RANKED_FLEX_SR");
const unavailable: RankedMe = { status: "UNAVAILABLE", reason: "NO_OBSERVATION", position: null, change: null };
const unranked: RankedMe = { ...flex, status: "UNRANKED", position: { ...flex.position!, state: "UNRANKED", tier: null, division: null, leaguePoints: null } };

describe("fila inicial pela melhor classificação disponível", () => {
  it.each([
    [unavailable, flex, "RANKED_FLEX_SR"],
    [unranked, flex, "RANKED_FLEX_SR"],
    [solo, unavailable, "RANKED_SOLO_5x5"],
    [unavailable, unranked, "RANKED_FLEX_SR"],
    [undefined, undefined, "RANKED_SOLO_5x5"],
    [unavailable, unavailable, "RANKED_SOLO_5x5"],
    [solo, flex, "RANKED_SOLO_5x5"],
    [solo, observation("RANKED_FLEX_SR", "PLATINUM", "IV", 0), "RANKED_FLEX_SR"],
    [observation("RANKED_SOLO_5x5", "GOLD", "II", 0), observation("RANKED_FLEX_SR", "GOLD", "III", 99), "RANKED_SOLO_5x5"],
    [solo, observation("RANKED_FLEX_SR", "GOLD", "IV", 30), "RANKED_FLEX_SR"],
    [observation("RANKED_SOLO_5x5", "MASTER", null, 200), observation("RANKED_FLEX_SR", "MASTER", null, 300), "RANKED_FLEX_SR"],
    [observation("RANKED_SOLO_5x5", "GRANDMASTER", null, 200), observation("RANKED_FLEX_SR", "MASTER", null, 999), "RANKED_SOLO_5x5"],
    [solo, { ...observation("RANKED_FLEX_SR", "DIAMOND"), status: "STALE" }, "RANKED_SOLO_5x5"],
    [unavailable, { ...flex, status: "STALE" }, "RANKED_FLEX_SR"],
  ] as const)("escolhe corretamente no caso %#", (soloData, flexData, expected) => {
    expect(preferredQueue(soloData, flexData)).toBe(expected);
  });
});
