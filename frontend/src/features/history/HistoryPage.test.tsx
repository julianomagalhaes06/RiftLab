import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    expect(within(screen.getByRole("list", { name: "Partidas carregadas" })).getByText("BR1_123")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "kai" } });
    expect(within(screen.getByRole("list", { name: "Partidas carregadas" })).getByText("BR1_123")).toBeInTheDocument();
    expect(screen.queryByText("DEMO-0008")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "DEMO" }));
    expect(screen.getByText("Nenhuma partida neste filtro")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
    expect(within(screen.getByRole("list", { name: "Partidas carregadas" })).getByText("DEMO-0008")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Todas as origens" }));
    fireEvent.click(screen.getByRole("button", { name: "Carregar mais partidas" }));
    expect(await within(screen.getByRole("list", { name: "Partidas carregadas" })).findByText("BR1_456")).toBeInTheDocument();
    expect(screen.getByText("3 partidas carregadas")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Vitórias" })).not.toBeInTheDocument();
    expect(screen.getByText(/Filtro de resultado indisponível/)).toBeInTheDocument();
  });

  it("seleciona metadados locais sem buscar detalhe por linha e acompanha o filtro", async () => {
    atHistory();
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/matches$/, () => json(200, { items: [
      matchSummary({ matchId: "BR1_111", championsPresent: [{ championId: 51, name: "Caitlyn" }] }),
      matchSummary({ matchId: "DEMO-0008", championsPresent: [] }),
    ], nextCursor: null }));
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);
    const select = await screen.findByRole("button", { name: "Selecionar detalhes da partida DEMO-0008" });
    fireEvent.click(select);
    expect(select).toHaveAttribute("aria-pressed", "true");
    const inspector = screen.getByRole("complementary", { name: "Detalhes da partida" });
    expect(within(inspector).getByText("DEMO-0008")).toBeInTheDocument();
    expect(within(inspector).getByText("Demonstração ilustrativa")).toBeInTheDocument();
    expect(within(inspector).getByText(/Nomes não disponíveis/)).toBeInTheDocument();
    expect(within(inspector).getByRole("link", { name: "Abrir Contexto" })).toHaveAttribute("href", "/historico/DEMO-0008");
    fireEvent.click(screen.getByRole("button", { name: "RIOT" }));
    expect(within(inspector).getByText("BR1_111")).toBeInTheDocument();
    expect(within(inspector).getByText("Limitado: patch sem avaliação")).toBeInTheDocument();
    expect(api.callsTo("GET", /^\/api\/v1\/matches\/[^/]+$/)).toHaveLength(0);
    expect(api.callsTo("GET", /^\/api\/v1\/matches$/)).toHaveLength(1);
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
