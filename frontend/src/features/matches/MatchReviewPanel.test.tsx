import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MatchParticipant, MatchReview } from "../../shared/api/client";
import { createMockApi, json } from "../../test/mockApi";
import { RouterProvider } from "../../shared/router/RouterProvider";
import { MatchReviewPanel } from "./MatchReviewPanel";

function renderPanel() {
  render(
    <RouterProvider>
      <MatchReviewPanel matchId="BR1_1" participant={participants[0]} participants={participants} />
    </RouterProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

const participants = [1, 6].map((participantId) => ({
  participantId,
  championId: participantId === 1 ? 86 : 122,
  team: participantId === 1 ? 100 : 200,
  role: "TOP",
  champion: { championId: participantId === 1 ? 86 : 122, name: participantId === 1 ? "Garen" : "Darius", dataDragonVersion: "16.19.1", key: "x", imageUrl: null },
})) as unknown as MatchParticipant[];

function review(overrides: Partial<MatchReview> = {}): MatchReview {
  return {
    matchId: "BR1_1",
    participantId: 1,
    championId: 86,
    role: "TOP",
    patch: "16.20",
    queue: 420,
    referenceVersion: "16.19-solo-v1",
    referenceInherited: true,
    purchases: [
      { decisionId: "00000000-0000-4000-8000-000000000001", timestampMs: 540_000, ordinal: 1, itemId: 3078, status: "READY", label: "COMMON", share: 0.52, rank: 1, support: 310, referenceTop: [{ itemId: 3078, share: 0.52 }, { itemId: 6631, share: 0.2 }] },
      { decisionId: "00000000-0000-4000-8000-000000000002", timestampMs: 900_000, ordinal: 2, itemId: 3742, status: "READY", label: "UNCOMMON", share: 0.01, rank: 9, support: 120, referenceTop: [{ itemId: 3053, share: 0.3 }] },
      { decisionId: "00000000-0000-4000-8000-000000000003", timestampMs: 1_300_000, ordinal: 3, itemId: 3053, status: "NOT_READY", label: null, share: null, rank: null, support: null, referenceTop: [] },
    ],
    skills: { priority: "E>Q>W", label: "ALTERNATIVE", share: 0.22, support: 300, mostCommon: [{ priority: "Q>E>W", share: 0.7 }, { priority: "E>Q>W", share: 0.22 }] },
    lane: { opponentId: 6, minutes: [{ minute: 10, own: { gold: 3500, xp: 4100, cs: 80, level: 8 }, opponent: { gold: 3200, xp: 4300, cs: 76, level: 9 } }] },
    winProbability: {
      modelVersion: "win-probability-16.19-v1",
      team: 100,
      points: [60_000, 120_000, 180_000].map((timestampMs, i) => ({ timestampMs, probability: [0.5, 0.31, 0.45][i] })),
      keyMoments: [{ minute: 2, delta: -0.19, probabilityAfter: 0.31, events: [{ kind: "DEATH", team: 200, involvesParticipant: true }, { kind: "CHAMPION_KILL", team: 200, involvesParticipant: false }, { kind: "CHAMPION_KILL", team: 200, involvesParticipant: false }, { kind: "DRAGON", team: 200, involvesParticipant: false }] }],
    },
    winProbabilityUnavailable: null,
    counts: { purchases: 3, ready: 2, common: 1, alternative: 0, uncommon: 1, noReference: 0 },
    ...overrides,
  };
}

describe("revisão da partida", () => {
  it("rotula compras, habilidades, fase de rotas e momentos-chave com linguagem de popularidade", async () => {
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/matches\/BR1_1\/review$/, (call) => {
      expect(call.search.get("participantId")).toBe("1");
      return json(200, review());
    });
    vi.stubGlobal("fetch", api.fetch);
    renderPanel();

    await screen.findByText(/compras analisadas/);
    const section = screen.getByRole("region", { name: "Revisão da partida" });
    expect(section).toHaveTextContent("Referência do 16.19 Solo/Duo; esta partida é do 16.20");
    expect(section).toHaveTextContent("Comum não quer dizer certo");
    expect(section).toHaveTextContent("2 compras analisadas de 3: 1 comum, 0 alternativas, 1 incomum.");
    const rows = within(section).getAllByRole("listitem").slice(0, 3);
    expect(rows[0]).toHaveTextContent("Comum");
    expect(rows[0]).toHaveTextContent("Escolhida por 52% de 310 jogadores nesta etapa (1ª mais comum).");
    expect(rows[1]).toHaveTextContent("Incomum");
    expect(rows[2]).toHaveTextContent("Fora da análise");
    expect(section).toHaveTextContent("E › Q › W, uma alternativa frequente (22% dos jogadores). A mais comum é Q › E › W (70%)");
    expect(within(section).getByRole("table")).toHaveTextContent("+300 vs Darius");
    expect(section).toHaveTextContent("2 min: −19 pontos percentuais (você morreu, 2 abates do adversário, dragão do adversário).");
    expect(screen.getByRole("img", { name: /no fim, 45%/ })).toBeInTheDocument();
  });

  it("sem referência nem modelo, explica o que falta", async () => {
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/matches\/BR1_1\/review$/, () =>
      json(200, review({ referenceVersion: null, purchases: [], skills: null, lane: null, winProbability: null, winProbabilityUnavailable: "FRAMES_WITHOUT_XP", counts: { purchases: 0, ready: 0, common: 0, alternative: 0, uncommon: 0, noReference: 0 } })),
    );
    vi.stubGlobal("fetch", api.fetch);
    renderPanel();
    await screen.findByText(/A referência ainda não está disponível/);
    const section = screen.getByRole("region", { name: "Revisão da partida" });
    expect(section).toHaveTextContent("A referência ainda não está disponível neste ambiente.");
    expect(section).toHaveTextContent("O gráfico precisa dos quadros com XP: prepare a partida de novo.");
  });
});
