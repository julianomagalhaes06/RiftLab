import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../app/App";
import { createMockApi, json, matchSummary, problem } from "../../test/mockApi";

function atPanel() { window.history.replaceState(null, "", "/painel"); }
afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

const noRank = { status: "UNAVAILABLE", reason: "NO_OBSERVATION", position: null, change: null };

describe("Painel com partidas importadas recentes", () => {
  it("mantém um estado de carregamento identificável enquanto a lista aguarda resposta", async () => {
    atPanel();
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/ranked\/me$/, () => json(200, noRank));
    let release!: (response: Response) => void;
    api.on("GET", /^\/api\/v1\/matches$/, () => new Promise<Response>((resolve) => { release = resolve; }));
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);
    expect(within(await screen.findByRole("region", { name: "Partidas importadas recentes" })).getByRole("status")).toHaveTextContent("Carregando partidas recentes");
    // O estado de carregamento pode aparecer antes de a chamada sair: espera a chamada existir.
    await waitFor(() => expect(release).toBeTypeOf("function"));
    release(json(200, { items: [], nextCursor: null }));
    expect(await screen.findByText("Nenhuma partida importada")).toBeInTheDocument();
  });

  it("pede uma só página de cinco, exibe apenas itens retornados e não atribui campeão à conta", async () => {
    atPanel();
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/ranked\/me$/, () => json(200, noRank));
    api.on("GET", /^\/api\/v1\/matches$/, () => json(200, { items: [
      matchSummary({ matchId: "BR1_3111", championsPresent: [{ championId: 51, name: "Caitlyn" }] }),
      matchSummary({ matchId: "DEMO-0008", championsPresent: [] }),
    ], nextCursor: "more" }));
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);
    const list = await screen.findByRole("list");
    expect(within(list).getByText("BR1_3111")).toBeInTheDocument();
    expect(within(list).getByText("Campeões presentes: Caitlyn")).toBeInTheDocument();
    expect(within(list).getByText("Campeões presentes: não disponíveis")).toBeInTheDocument();
    expect(screen.getByText(/Mostrando 2 partidas importadas recentes/)).toBeInTheDocument();
    expect(screen.getByText(/Total no servidor não informado/)).toBeInTheDocument();
    expect(screen.getByText(/Ainda não há observação desta fila/)).toBeInTheDocument();
    expect(screen.queryByText(/vitória|derrota|KDA|tropas/i)).not.toBeInTheDocument();
    const calls = api.callsTo("GET", /^\/api\/v1\/matches$/);
    expect(calls).toHaveLength(1);
    expect(calls[0].search.get("limit")).toBe("5");
  });

  it("mostra estado vazio com ação e recupera falha pelo botão de retry", async () => {
    atPanel();
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/ranked\/me$/, () => json(200, noRank));
    let attempts = 0;
    api.on("GET", /^\/api\/v1\/matches$/, () => {
      attempts += 1;
      return attempts === 1 ? problem(503, "SERVICE_UNAVAILABLE") : json(200, { items: [], nextCursor: null });
    });
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Tentar novamente" }));
    expect(await screen.findByText("Nenhuma partida importada")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Partidas importadas recentes" })).getByRole("link", { name: "Importar ou abrir demonstração" })).toHaveAttribute("href", "/importar");
    await waitFor(() => expect(api.callsTo("GET", /^\/api\/v1\/matches$/)).toHaveLength(2));
  });
});
