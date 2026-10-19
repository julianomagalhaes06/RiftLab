import type { ChampionPatterns } from "../shared/api/client";

export function championPatterns(overrides: Partial<ChampionPatterns> = {}): ChampionPatterns {
  return {
    patches: ["16.19", "16.18"], patch: "16.19", role: "BOTTOM", phase: "sequence",
    champions: [
      { championId: 51, champion: { name: "Caitlyn", dataDragonVersion: "16.19.1", imageFile: "Caitlyn.png" }, matches: 8 },
      { championId: 222, champion: { name: "Jinx", dataDragonVersion: "16.19.1", imageFile: "Jinx.png" }, matches: 2 },
      { championId: 21, champion: { name: "Miss Fortune", dataDragonVersion: "16.19.1", imageFile: "MissFortune.png" }, matches: 0 },
    ],
    championId: 51, source: "IMPORTED_REAL_MATCHES", sampleLimit: 200,
    totalParticipations: 8, sampledParticipations: 8, observedDecisions: 16,
    eligibleDecisions: 12, patternDenominator: 5, minimumSample: 5, status: "AVAILABLE",
    patterns: [{ items: [
      { itemId: 3031, name: "Infinity Edge", dataDragonVersion: "16.19.1" },
      { itemId: 3036, name: "Lord Dominik's Regards", dataDragonVersion: "16.19.1" },
    ], count: 3, denominator: 5 }],
    situations: [{ matchId: "BR1_123", participantId: 4,
      decisionId: "11111111-1111-4111-8111-111111111111", timestampMs: 600000 }],
    ...overrides,
  };
}
