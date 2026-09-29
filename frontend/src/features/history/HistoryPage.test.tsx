import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../app/App";
import { createMockApi, json, matchSummary, problem } from "../../test/mockApi";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

function atHistory() {
  window.history.replaceState(null, "", "/historico");
}

describe("Histórico com dados realmente disponíveis", () => {
  it("busca por ID e campeão presente nas páginas carregadas, filtra origem e carrega mais", async () => {
    atHistory();
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/matches$/, ({ search }) =>
      json(200, search.has("cursor")
        ? { items: [matchSummary({ matchId: "BR1_456", championsPresent: [{ championId: 51, name: "Caitlyn" }] })], nextCursor: null }
        : { items: [
            matchSummary({ matchId: "DEMO-0008" }),
            matchSummary({ matchId: "BR1_123", championsPresent: [{ championId: 145, name: "Kai'Sa" }] }),
          ], nextCursor: "next" }));
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);

    expect(await screen.findByText("2 partidas carregadas")).toBeInTheDocument();
    expect(screen.getByText("1 RIOT · 1 DEMO")).toBeInTheDocument();
    expect(screen.getByText("BR1_123")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "kai" } });
    expect(screen.getByText("BR1_123")).toBeInTheDocument();
    expect(screen.queryByText("DEMO-0008")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "DEMO" }));
    expect(screen.getByText("Nenhuma partida neste filtro")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
    expect(screen.getByText("DEMO-0008")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Todas as origens" }));
    fireEvent.click(screen.getByRole("button", { name: "Carregar mais partidas" }));
    expect(await screen.findByText("BR1_456")).toBeInTheDocument();
    expect(screen.getByText("3 partidas carregadas")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Vitórias" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Derrotas" })).toBeDisabled();
  });

  it("orienta lista vazia e recupera erro de API", async () => {
    atHistory();
    const api = createMockApi();
    let attempts = 0;
    api.on("GET", /^\/api\/v1\/matches$/, () => {
      attempts += 1;
      return attempts === 1 ? problem(503, "SERVICE_UNAVAILABLE") : json(200, { items: [], nextCursor: null });
    });
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Tentar novamente" }));
    expect(await screen.findByText("Nenhuma partida importada")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Importar ou abrir demonstração" })).toHaveAttribute("href", "/");
  });

  it("Contexto abre rota profunda da partida e mantém percurso de participante", async () => {
    atHistory();
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/matches$/, () => json(200, { items: [matchSummary()], nextCursor: null }));
    api.on("GET", /^\/api\/v1\/matches\/DEMO-0008$/, () => json(200, {
      ...matchSummary(),
      participants: [{ participantId: 1, championId: 9001, team: 100, role: "BOTTOM", champion: null }],
      preparation: { status: "READY", errorCode: null, retryAt: null, attempts: 1 },
    }));
    api.on("GET", /^\/api\/v1\/matches\/DEMO-0008\/decisions$/, () => json(200, { items: [], nextCursor: null }));
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);
    fireEvent.click(await screen.findByRole("link", { name: "Contexto da partida DEMO-0008" }));
    expect(window.location.pathname).toBe("/historico/DEMO-0008");
    fireEvent.click(await screen.findByRole("link", { name: /Participante 1/ }));
    await waitFor(() => expect(window.location.search).toBe("?participant=1"));
    expect(screen.getByText(/Nenhuma decisão/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Voltar ao Histórico" })).toHaveAttribute("href", "/historico");
  });
});
