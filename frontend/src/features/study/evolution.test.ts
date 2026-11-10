import { describe, expect, it } from "vitest";
import type { LearningAttemptSummary } from "../../shared/api/client";
import { weekLabel, weeklyBuckets } from "./evolution";

function attempt(createdAt: string, hasReflection = false): LearningAttemptSummary {
  return {
    attemptId: `attempt-${createdAt}-${Math.random()}`,
    decisionId: "decision-1",
    matchId: "DEMO-0008",
    participantId: 1,
    status: "REVEALED",
    mode: "STUDY",
    skipped: false,
    hasReflection,
    analysisId: null,
    createdAt,
    revealedAt: null,
  };
}

describe("weeklyBuckets", () => {
  it("agrupa por semana (segunda a domingo), contando tentativas e reflexões", () => {
    // Segunda 2026-11-02 e domingo 2026-11-08 são a mesma semana; terça seguinte é outra.
    const buckets = weeklyBuckets(
      [
        attempt("2026-11-02T10:00:00Z"),
        attempt("2026-11-08T23:00:00Z", true),
        attempt("2026-11-10T08:00:00Z"),
      ],
      12,
    );
    expect(buckets).toEqual([
      { weekStart: "2026-11-02", attempts: 2, reflections: 1 },
      { weekStart: "2026-11-09", attempts: 1, reflections: 0 },
    ]);
  });

  it("devolve só as N semanas mais recentes, da mais antiga para a mais nova", () => {
    const attempts = [
      attempt("2026-10-05T12:00:00Z"),
      attempt("2026-10-12T12:00:00Z"),
      attempt("2026-10-19T12:00:00Z"),
    ];
    expect(weeklyBuckets(attempts, 2).map((b) => b.weekStart)).toEqual(["2026-10-12", "2026-10-19"]);
  });

  it("lista vazia não quebra", () => {
    expect(weeklyBuckets([], 12)).toEqual([]);
  });
});

describe("weekLabel", () => {
  it("mostra o intervalo segunda–domingo dentro do mesmo mês", () => {
    expect(weekLabel("2026-11-02")).toBe("02–08 nov");
  });

  it("mostra os dois meses quando a semana cruza a virada", () => {
    expect(weekLabel("2026-10-30")).toBe("30 out–05 nov");
  });
});
