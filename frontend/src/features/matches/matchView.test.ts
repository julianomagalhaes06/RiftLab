import { describe, expect, it } from "vitest";
import type { DecisionSummary, MatchParticipant, MatchTimeline, TimelineItemEvent } from "../../shared/api/client";
import {
  completedBuild,
  decisionsInVisit,
  goldDiffSeries,
  maxOrder,
  objectiveCounts,
  shopVisits,
  skillOrder,
  summarizeDecisions,
  teamTotals,
} from "./matchView";

const item = (timestampMs: number, itemId: number, kind: "PURCHASE" | "SOLD" = "PURCHASE", itemClass: string | null = "COMPONENT"): TimelineItemEvent =>
  ({ timestampMs, itemId, kind, itemClass });
const player = (participantId: number, team: number, kills: number | null, win: boolean): MatchParticipant =>
  ({ participantId, championId: participantId, team, role: null, win, kills, deaths: 1, assists: 2, champion: null });

describe("matchView", () => {
  it("soma o placar do time só com dados completos", () => {
    const players = [player(1, 100, 3, true), player(2, 100, 4, true), player(6, 200, null, false)];
    expect(teamTotals(players, 100)).toMatchObject({ kills: 7, deaths: 2, win: true });
    expect(teamTotals(players, 200)).toMatchObject({ kills: null, win: false });
  });

  it("agrupa compras próximas na mesma volta à loja e liga a decisão à visita", () => {
    const visits = shopVisits([item(60_000, 1), item(70_000, 2), item(300_000, 3), item(330_000, 4)]);
    expect(visits.map((v) => v.items.map((i) => i.itemId))).toEqual([[1, 2], [3, 4]]);
    const decision = { timestampMs: 330_000 } as DecisionSummary;
    expect(decisionsInVisit(visits[1], [decision])).toEqual([decision]);
    expect(decisionsInVisit(visits[0], [decision])).toEqual([]);
  });

  it("build concluída ignora componentes e itens vendidos depois", () => {
    const build = completedBuild([
      item(1, 1001, "PURCHASE", "BOOTS"),
      item(2, 1036),
      item(3, 3031, "PURCHASE", "MAIN"),
      item(4, 3006, "PURCHASE", "MAIN"),
      item(5, 3006, "SOLD", "MAIN"),
    ]);
    expect(build.map((i) => i.itemId)).toEqual([1001, 3031]);
  });

  it("ordem de habilidades e de maximização", () => {
    const skills = [1, 3, 2, 1, 1, 4, 1, 3, 1, 3, 4, 3, 3].map((skillSlot, i) => ({ timestampMs: i, skillSlot, levelUpType: "NORMAL" }));
    expect(skillOrder([...skills, { timestampMs: 99, skillSlot: 4, levelUpType: "EVOLVE" }])).toHaveLength(13);
    expect(maxOrder(skillOrder(skills))).toEqual([1, 3, 2]);
  });

  it("diferença de ouro descarta quadro incompleto e conta objetivos do time", () => {
    const players = [player(1, 100, 0, true), player(2, 200, 0, false)];
    const timeline = {
      matchId: "BR1_1", participants: [], kills: [],
      objectives: [{ timestampMs: 1, kind: "DRAGON", team: 100 }, { timestampMs: 2, kind: "TOWER", team: 200 }],
      goldFrames: [{ timestampMs: 0, gold: [500, 500] }, { timestampMs: 60_000, gold: [900, null] }, { timestampMs: 120_000, gold: [1500, 1200] }],
    } as MatchTimeline;
    expect(goldDiffSeries(timeline, players).map((p) => [p.minute, p.diff])).toEqual([[0, 0], [2, 300]]);
    expect(objectiveCounts(timeline.objectives, 100).DRAGON).toBe(1);
    expect(objectiveCounts(timeline.objectives, 100).TOWER).toBe(0);
  });

  it("resume motivos de bloqueio uma vez, do mais comum ao menos", () => {
    const summary = summarizeDecisions([
      { eligible: false, reasonCodes: ["OUT_OF_SCOPE", "INVENTORY_UNCERTAIN"] },
      { eligible: false, reasonCodes: ["OUT_OF_SCOPE"] },
      { eligible: true, reasonCodes: [] },
    ] as unknown as DecisionSummary[]);
    expect(summary).toEqual({ total: 3, eligible: 1, reasons: [["OUT_OF_SCOPE", 2], ["INVENTORY_UNCERTAIN", 1]] });
  });

  it("separa elegibilidade persistida da disponibilidade atual e preserva a causa da suspensão", () => {
    const summary = summarizeDecisions([
      { eligible: true, reconstructionRulesHealth: "SUSPENDED", reasonCodes: [] },
      { eligible: true, reconstructionRulesHealth: "UNVERIFIED", reasonCodes: [] },
    ] as unknown as DecisionSummary[]);
    expect(summary.eligible).toBe(1);
    expect(summary.reasons).toEqual([["INHERITED_RULES_SUSPENDED", 1]]);
  });
});
