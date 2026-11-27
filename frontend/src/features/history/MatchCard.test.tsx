import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MatchSummary } from "../../shared/api/client";
import { RouterProvider } from "../../shared/router/RouterProvider";
import { createMockApi, json, matchDetail, matchSummary } from "../../test/mockApi";
import { MatchCard } from "./MatchCard";

afterEach(() => {
  vi.unstubAllGlobals();
});

const PLAYERS = [
  { participantId: 1, championId: 9001, team: 100, role: "BOTTOM", kills: 5, deaths: 2, assists: 7, isLocal: true },
  { participantId: 2, championId: 9002, team: 200, role: "TOP", kills: 1, deaths: 4, assists: 3 },
];

function renderExpandable(matchId: string) {
  renderCard({ matchId, participants: PLAYERS, localParticipantId: 1 });
  fireEvent.click(screen.getByRole("button", { name: "Mostrar placar da partida" }));
}

function renderCard(overrides: Record<string, unknown>) {
  const match = matchSummary({ matchId: "BR1_1", ...overrides }) as unknown as MatchSummary;
  render(
    <RouterProvider>
      <ul>
        <MatchCard match={match} />
      </ul>
    </RouterProvider>,
  );
}

describe("selo da revisão no cartão do Histórico", () => {
  it("mostra quantas compras da conta foram comparadas com a referência", () => {
    renderCard({ reviewPurchases: { ready: 2, total: 3 } });
    expect(screen.getByText("Revisão: 2 de 3 compras")).toBeInTheDocument();
  });

  it("avisa quando nenhuma compra pôde ser revisada", () => {
    renderCard({ reviewPurchases: { ready: 0, total: 2 } });
    expect(screen.getByText("Sem compras para revisar")).toBeInTheDocument();
  });

  it("sem dados de revisão, não mostra selo", () => {
    renderCard({ reviewPurchases: null });
    expect(screen.queryByText(/Revisão:|Sem compras para revisar/)).not.toBeInTheDocument();
  });
});

describe("perfil dos jogadores no placar expandido do Histórico", () => {
  it("ao abrir o placar, busca o Riot ID da partida e liga cada jogador ao perfil", async () => {
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/matches\/BR1_1$/, () => json(200, matchDetail({
      matchId: "BR1_1",
      participants: [
        { participantId: 1, championId: 9001, team: 100, gameName: "Eu Mesmo", tagLine: "BR1" },
        { participantId: 2, championId: 9002, team: 200, gameName: "Rival", tagLine: "TOP1" },
      ],
    })));
    vi.stubGlobal("fetch", api.fetch);
    renderCard({ matchId: "BR1_1", participants: PLAYERS, localParticipantId: 1 });
    expect(api.callsTo("GET", /^\/api\/v1\/matches\/BR1_1$/)).toHaveLength(0); // só no clique

    fireEvent.click(screen.getByRole("button", { name: "Mostrar placar da partida" }));
    const rival = await screen.findByRole("link", { name: "Perfil de Rival#TOP1" });
    expect(rival).toHaveAttribute("href", "/jogador/Rival-TOP1?partida=BR1_1&participante=2");
    expect(screen.getByRole("link", { name: "Perfil de Eu Mesmo#BR1" })).toHaveAttribute(
      "href", "/jogador/Eu%20Mesmo-BR1?partida=BR1_1&participante=1",
    );

    fireEvent.click(screen.getByRole("button", { name: "Ocultar placar da partida" }));
    fireEvent.click(screen.getByRole("button", { name: "Mostrar placar da partida" }));
    expect(await screen.findByRole("link", { name: "Perfil de Rival#TOP1" })).toBeInTheDocument();
    expect(api.callsTo("GET", /^\/api\/v1\/matches\/BR1_1$/)).toHaveLength(1); // reaproveita
  });

  it("sem resposta do detalhe, o placar abre igual, só sem os links", async () => {
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/matches\/BR1_2$/, () => json(404, { code: "NOT_FOUND" }));
    vi.stubGlobal("fetch", api.fetch);
    renderExpandable("BR1_2");
    expect(await screen.findByRole("table", { name: /Time azul/ })).toBeInTheDocument();
    await vi.waitFor(() => expect(api.callsTo("GET", /^\/api\/v1\/matches\/BR1_2$/)).toHaveLength(1));
    expect(screen.queryByRole("link", { name: /^Perfil de / })).not.toBeInTheDocument();
  });

  it("partida de demonstração não busca nomes", () => {
    const api = createMockApi();
    vi.stubGlobal("fetch", api.fetch);
    renderExpandable("DEMO-0008");
    expect(screen.getByRole("table", { name: /Time azul/ })).toBeInTheDocument();
    expect(api.callsTo("GET", /^\/api\/v1\/matches\//)).toHaveLength(0);
  });
});
