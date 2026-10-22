import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../app/App";
import { createMockApi, json, matchSummary } from "../../test/mockApi";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const PLAYER_ID = "7a5c3f1e-0000-4000-8000-000000000001";

function profile(overrides: Record<string, unknown> = {}) {
  return {
    id: PLAYER_ID,
    gameName: "Pão de Queijo",
    tagLine: "BR1",
    profileIconId: 4567,
    summonerLevel: 321,
    refreshedAt: "2026-10-19T15:00:00Z",
    dataDragonVersion: "16.20.1",
    solo: { queue: "RANKED_SOLO_5x5", state: "READY", tier: "GOLD", division: "II", leaguePoints: 41, wins: 10, losses: 8, observedAt: "2026-10-19T15:00:00Z" },
    flex: { queue: "RANKED_FLEX_SR", state: "UNRANKED", observedAt: "2026-10-19T15:00:00Z" },
    staleReason: null,
    ...overrides,
  };
}

function setup(path: string, configure: (api: ReturnType<typeof createMockApi>) => void) {
  window.history.replaceState(null, "", path);
  const api = createMockApi();
  api.on("GET", /^\/api\/v1\/dashboard$/, () => json(503, {}));
  api.on("GET", /^\/api\/v1\/ranked\/me$/, () => json(200, { status: "UNAVAILABLE", reason: "NO_OBSERVATION", position: null, change: null }));
  api.on("GET", /^\/api\/v1\/matches$/, () => json(200, { items: [], nextCursor: null }));
  configure(api);
  vi.stubGlobal("fetch", api.fetch);
  render(<App />);
  return api;
}

describe("perfil de jogador por Riot ID", () => {
  it("busca no topo leva ao perfil com ícone, nível, elo e as partidas do jogador", async () => {
    const api = setup("/", (mock) => {
      mock.on("POST", /^\/api\/v1\/players\/lookup$/, () => json(200, profile()));
      mock.on("POST", /\/players\/.+\/sync$/, () => json(202, { state: "QUEUED" }));
      mock.on("GET", /\/players\/.+\/sync$/, () => json(200, { state: "SUCCEEDED", newEnqueued: 0, finishedAt: "2026-10-19T15:00:00Z" }));
      mock.on("GET", /^\/api\/v1\/matches$/, (call) =>
        json(200, call.search.get("player") ? { items: [matchSummary({ matchId: "BR1_42", localParticipantId: 3 })], nextCursor: null } : { items: [], nextCursor: null }),
      );
    });
    const search = await screen.findByRole("searchbox", { name: "Buscar jogador pelo Riot ID" });
    fireEvent.change(search, { target: { value: "Nome sem tag" } });
    fireEvent.submit(search);
    const form = screen.getByRole("search", { name: "Buscar jogador" });
    expect(await within(form).findByRole("alert")).toHaveTextContent("Use o formato Nome#TAG");

    fireEvent.change(search, { target: { value: "Pão de Queijo#BR1" } });
    fireEvent.submit(search);
    expect(window.location.pathname).toBe("/jogador/P%C3%A3o%20de%20Queijo-BR1");
    expect(await screen.findByText(/Nível 321/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Pão de Queijo #BR1/ })).toBeInTheDocument();
    const icon = document.querySelector(".player-header__icon img");
    expect(icon).toHaveAttribute("src", "https://ddragon.leagueoflegends.com/cdn/16.20.1/img/profileicon/4567.png");
    // CC-1204: um card de elo por fila, o mesmo de Minha posição, sem lista própria de elos.
    const ranks = screen.getByRole("group", { name: "Elo" });
    const [solo, flex] = [...ranks.querySelectorAll(".rank-card")];
    expect(solo).toHaveAttribute("data-tier", "GOLD");
    expect(solo).toHaveTextContent("Elo Ouro II, 41 de 100 PDL");
    expect(solo).toHaveTextContent("10 vitórias · 8 derrotas · 56%");
    expect(solo).toHaveTextContent("Faltam 59 PDL para Ouro I");
    expect(flex).toHaveAttribute("data-tier", "NONE");
    expect(flex).toHaveTextContent(/^Flex.*Sem classificação$/);
    expect(await screen.findByRole("link", { name: "Contexto da partida BR1_42" })).toBeInTheDocument();
    const lookups = api.callsTo("POST", /\/players\/lookup$/);
    expect(lookups[0].body).toEqual({ gameName: "Pão de Queijo", tagLine: "BR1", refresh: false });
    expect(api.callsTo("GET", /^\/api\/v1\/matches$/).some((call) => call.search.get("player") === PLAYER_ID)).toBe(true);
  });

  it("Atualizar consulta a Riot de novo e pede a sincronização das partidas", async () => {
    const api = setup("/jogador/Pão de Queijo-BR1", (mock) => {
      mock.on("POST", /^\/api\/v1\/players\/lookup$/, () => json(200, profile()));
      mock.on("POST", /\/players\/.+\/sync$/, () => json(200, { state: "SUCCEEDED", reason: "COOLDOWN", newEnqueued: 0, finishedAt: "2026-10-19T15:00:00Z", nextAllowedAt: "2000-01-01T00:00:00Z" }));
    });
    const button = await screen.findByRole("button", { name: "Atualizar" });
    fireEvent.click(button);
    await vi.waitFor(() => {
      expect(api.callsTo("POST", /\/players\/lookup$/).some((call) => (call.body as { refresh: boolean }).refresh)).toBe(true);
      expect(api.callsTo("POST", /\/players\/.+\/sync$/).length).toBeGreaterThanOrEqual(2);
    });
    // O banco guarda só o HMAC do Riot ID: cada pedido de sincronização traz o Riot ID.
    for (const call of api.callsTo("POST", /\/players\/.+\/sync$/)) {
      expect(call.body).toEqual({ gameName: "Pão de Queijo", tagLine: "BR1" });
    }
  });

  it.each([
    ["PLAYER_NOT_FOUND", 404, "Nenhuma conta Riot com esse Riot ID"],
    ["PLAYER_NOT_ON_PLATFORM", 404, "não joga League of Legends no servidor BR"],
    ["RIOT_RATE_LIMITED", 503, "Muitas consultas à Riot agora"],
  ])("erro %s vira texto útil", async (code, status, text) => {
    setup("/jogador/Ninguem-000", (mock) => {
      mock.on("POST", /^\/api\/v1\/players\/lookup$/, () => json(status, { code, status, title: "x", detail: "x", type: "x", requestId: "r" }));
    });
    expect(await screen.findByText(text, { exact: false })).toHaveAttribute("role", "alert");
    expect(screen.getByRole("heading", { name: /Ninguem #000/ })).toBeInTheDocument();
  });
});
