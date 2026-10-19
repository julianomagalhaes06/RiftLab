import type { Dashboard } from "../shared/api/client";

export function dashboard(overrides: Partial<Dashboard> = {}): Dashboard {
  return {
    origin: "REAL", status: "READY", window: 20, queue: 420, queueSelection: "AUTO",
    queues: [{ queue: 420, totalMatches: 8 }, { queue: 440, totalMatches: 0 }], totalMatches: 8,
    sampledMatches: 8, winSamples: 7, wins: 4, winRate: 4 / 7,
    kdaSamples: 6, kda: 3.5, distinctChampions: 2,
    champions: [{ championId: 51, champion: null, matches: 5 }],
    recentMatches: [{ matchId: "BR1_3090001", endedAt: "2026-09-28T12:30:00Z", durationSeconds: 1800,
      participant: { participantId: 4, championId: 51, champion: null, team: 100, role: "BOTTOM",
        win: true, kills: 5, deaths: 0, assists: 2, totalMinionsKilled: 100, neutralMinionsKilled: 4 } }],
    progress: { attempts: 12, revealed: 8, reflections: 3, matches: 4 },
    ...overrides,
  };
}
