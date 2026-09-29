import { describe, expect, it } from "vitest";
import type { MatchSummary } from "../../shared/api/client";
import { matchSummary } from "../../test/mockApi";
import { filterLoadedMatches } from "./filter";

const matches = [
  matchSummary({ matchId: "DEMO-0008", championsPresent: [{ championId: 9001, name: null }] }),
  matchSummary({ matchId: "BR1_123", championsPresent: [{ championId: 145, name: "Kai'Sa" }] }),
  matchSummary({ matchId: "BR1_456", championsPresent: [{ championId: 51, name: "Caitlyn" }] }),
] as MatchSummary[];

describe("filtro do Histórico", () => {
  it("combina origem com busca em páginas carregadas, por ID ou campeão presente", () => {
    expect(filterLoadedMatches(matches, "kai", "RIOT").map((match) => match.matchId)).toEqual(["BR1_123"]);
    expect(filterLoadedMatches(matches, "br1_4", "ALL").map((match) => match.matchId)).toEqual(["BR1_456"]);
    expect(filterLoadedMatches(matches, "campeão #9001", "DEMO").map((match) => match.matchId)).toEqual(["DEMO-0008"]);
    expect(filterLoadedMatches(matches, "kai", "DEMO")).toEqual([]);
  });
});
