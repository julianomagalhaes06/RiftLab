import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MatchDetail } from "../../shared/api/client";
import { RouterProvider } from "../../shared/router/RouterProvider";
import { matchDetail } from "../../test/mockApi";
import { Scoreboard } from "./Scoreboard";

afterEach(() => vi.unstubAllGlobals());

function participant(id: number, overrides: Record<string, unknown> = {}) {
  return {
    participantId: id, championId: 100 + id, team: id <= 5 ? 100 : 200, role: "BOTTOM", win: id <= 5,
    champion: { name: `Campeão ${id}`, dataDragonVersion: "16.20.1", imageFile: "X.png" },
    kills: 2, deaths: 2, assists: 2, totalMinionsKilled: 100, neutralMinionsKilled: 0,
    summonerSpells: [4, 7], runes: { primaryStyle: 8000, keystone: 8008, secondaryStyle: 8300 },
    champLevel: 15, damageToChampions: 10000, goldEarned: 9000, wardsPlaced: 4, visionScore: 20, largestMultiKill: 1,
    ...overrides,
  };
}

const SPELLS = { data: { SummonerFlash: { key: "4", name: "Flash", image: { full: "SummonerFlash.png" } }, SummonerHeal: { key: "7", name: "Curar", image: { full: "SummonerHeal.png" } } } };
const RUNES = [
  { id: 8000, key: "Precision", name: "Precisão", icon: "perk-images/Styles/7201_Precision.png", slots: [{ runes: [{ id: 8008, name: "Ritmo Fatal", icon: "perk-images/Styles/Precision/LethalTempo/LethalTempoTemp.png" }] }] },
  { id: 8300, key: "Inspiration", name: "Inspiração", icon: "perk-images/Styles/7203_Whimsy.png", slots: [] },
];

describe("placar completo da partida", () => {
  it("mostra nível, feitiços, runas, dano com barra, ouro, sentinelas e visão", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(url.includes("summoner.json") ? SPELLS : url.includes("runesReforged") ? RUNES : {}), { status: 200 })));
    const match = matchDetail({
      matchId: "BR1_1", durationSeconds: 1800, localParticipantId: 1,
      participants: [participant(1, { damageToChampions: 20000, goldEarned: 12500, champLevel: 17 }), ...[2, 3, 4, 5, 6, 7, 8, 9, 10].map((id) => participant(id))],
    }) as unknown as MatchDetail;
    render(<RouterProvider><Scoreboard match={match} timeline={null} selected={1} localId={1} fromHistory /></RouterProvider>);
    const row = screen.getByRole("link", { name: /Participante 1 — Campeão 1/ }).closest("tr")!;
    expect(within(row).getByLabelText("Nível 17")).toHaveTextContent("17");
    expect(await within(row).findByText("Flash")).toBeInTheDocument();
    expect(within(row).getByText("Curar")).toBeInTheDocument();
    expect(await within(row).findByText("Ritmo Fatal")).toBeInTheDocument();
    expect(within(row).getByText("Inspiração")).toBeInTheDocument();
    expect(within(row).getByText("20.000")).toBeInTheDocument();
    expect(within(row).getByText("12,5 mil")).toBeInTheDocument();
    expect(within(row).getByText("20 de visão")).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader", { name: /Dano/ })).toHaveLength(2);
  });

  it("sem detalhes do bruto (DEMO ou partida antiga), mostra traço e nenhum feitiço", () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 404 })));
    const bare = { summonerSpells: null, runes: null, champLevel: null, damageToChampions: null, goldEarned: null, wardsPlaced: null, visionScore: null };
    const match = matchDetail({ matchId: "BR1_2", participants: [participant(1, bare), participant(6, bare)] }) as unknown as MatchDetail;
    render(<RouterProvider><Scoreboard match={match} timeline={null} selected={null} localId={null} fromHistory /></RouterProvider>);
    const row = screen.getByRole("link", { name: /Participante 1/ }).closest("tr")!;
    expect(within(row).queryByRole("group", { name: "Feitiços" })).not.toBeInTheDocument();
    expect(within(row).queryByLabelText(/Nível/)).not.toBeInTheDocument();
    expect(within(row).getAllByText("—").length).toBeGreaterThanOrEqual(3);
  });
});
