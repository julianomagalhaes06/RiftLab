import type { Backstage, RiotCallRow } from "../../shared/api/client";

/** Bastidores de exemplo para os testes (Vitest e Playwright). */
export function riotCall(id: number, overrides: Partial<RiotCallRow> = {}): RiotCallRow {
  return {
    id,
    at: new Date(Date.UTC(2026, 10, 15, 18, 0, id)).toISOString(),
    process: "worker",
    kind: "timeline",
    region: "AMERICAS",
    outcome: "HTTP_200",
    statusCode: 200,
    durationMs: 180 + id,
    attempt: 1,
    ...overrides,
  };
}

export function backstageFixture(overrides: Partial<Backstage> = {}): Backstage {
  return {
    generatedAt: new Date(Date.UTC(2026, 10, 15, 18, 0, 30)).toISOString(),
    riotKey: { status: "OK", checkedAt: new Date(Date.UTC(2026, 10, 15, 18, 0, 20)).toISOString() },
    lastHour: {
      calls: 46,
      succeeded: 44,
      authFailures: 0,
      rateLimited: 1,
      averageMs: 212,
      byKind: [
        { kind: "match", calls: 20, averageMs: 240 },
        { kind: "timeline", calls: 20, averageMs: 260 },
        { kind: "account", calls: 3, averageMs: 150 },
      ],
    },
    calls: [
      riotCall(12, { kind: "timeline" }),
      riotCall(11, { kind: "match" }),
      riotCall(10, { kind: "league", region: "BR1", process: "api" }),
      riotCall(9, { kind: "spectator", region: "BR1", process: "api", outcome: "HTTP_NOT_FOUND", statusCode: 404 }),
      riotCall(8, { kind: "account", process: "api" }),
    ],
    worker: {
      lastRiotCallAt: new Date(Date.UTC(2026, 10, 15, 18, 0, 12)).toISOString(),
      queue: [{ kind: "IMPORT", states: { RUNNING: 3, QUEUED: 5 } }, { kind: "PREPARE", states: { QUEUED: 2 } }],
      succeededToday: 128,
      failedToday: 2,
    },
    jobs: [
      {
        matchId: "BR1_3091234567",
        kind: "IMPORT",
        mode: "RIOT",
        state: "SUCCEEDED",
        attempts: 1,
        errorCode: null,
        createdAt: new Date(Date.UTC(2026, 10, 15, 18, 0, 0)).toISOString(),
        updatedAt: new Date(Date.UTC(2026, 10, 15, 18, 0, 2, 400)).toISOString(),
        fromSync: true,
      },
      {
        matchId: "BR1_3091234568",
        kind: "IMPORT",
        mode: "RIOT",
        state: "RUNNING",
        attempts: 1,
        errorCode: null,
        createdAt: new Date(Date.UTC(2026, 10, 15, 18, 0, 1)).toISOString(),
        updatedAt: new Date(Date.UTC(2026, 10, 15, 18, 0, 1)).toISOString(),
        fromSync: true,
      },
    ],
    syncs: [
      {
        state: "SUCCEEDED",
        requestedAt: new Date(Date.UTC(2026, 10, 15, 17, 59, 58)).toISOString(),
        finishedAt: new Date(Date.UTC(2026, 10, 15, 18, 0, 0)).toISOString(),
        listed: 20,
        newEnqueued: 8,
        alreadyKnown: 12,
        errorCode: null,
        steps: { accountMs: 182, listMs: 240, leagueMs: 95 },
      },
    ],
    counters: {
      matches: 412,
      participants: 4120,
      timelineEvents: 523811,
      decisions: 3290,
      studyAttempts: 41,
      playersSearched: 37,
      users: 4,
      linkedAccounts: 2,
    },
    ...overrides,
  };
}
