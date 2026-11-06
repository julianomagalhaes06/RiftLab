import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMockApi, json as jsonResponse } from "../test/mockApi";
import App from "./App";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

/** Além do health, o Painel e o Histórico consultam GET /matches. */
function stubApi(configure?: (api: ReturnType<typeof createMockApi>) => void) {
  const api = createMockApi();
  api.on("GET", /^\/api\/v1\/matches$/, () => jsonResponse(200, { items: [], nextCursor: null }));
  configure?.(api);
  vi.stubGlobal("fetch", api.fetch);
  return api;
}

describe("App: importar partida", () => {
  beforeEach(() => window.history.replaceState(null, "", "/importar"));

  it("mostra RiftLab, a demonstração e a faixa DEMO antes do health resolver", async () => {
    // Só a sessão responde (sem login exigido); o resto, incluindo o health, fica pendente.
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => String(input).endsWith("/api/v1/auth/session")
      ? Promise.resolve(new Response(JSON.stringify({ authRequired: false, user: null }), { headers: { "content-type": "application/json" } }))
      : new Promise(() => {})));

    render(<App />);

    expect(await screen.findByRole("heading", { name: "RiftLab" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Explorar demonstração" })).toBeInTheDocument();
    expect(await screen.findByRole("note")).toHaveTextContent("Partidas DEMO são sintéticas");
    expect(screen.getByRole("status")).toHaveTextContent("Verificando disponibilidade");
    // A lista crua de partidas saiu da entrada: elas ficam no Histórico.
    expect(screen.queryByRole("heading", { name: "Partidas importadas" })).not.toBeInTheDocument();
  });

  it("mostra serviço disponível quando live e ready respondem 200", async () => {
    stubApi();

    render(<App />);

    expect(await screen.findByText("Serviço disponível")).toBeInTheDocument();
  });

  it("mostra indisponibilidade quando a API nao responde", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network error")));

    render(<App />);

    expect(await screen.findByText("Serviço indisponível")).toBeInTheDocument();
    expect(screen.getByText(/API do RiftLab não respondeu/i)).toBeInTheDocument();
  });
});

describe("App: entrada", () => {
  it("abre o início em / com uma splash art do Data Dragon e o crédito da skin", async () => {
    stubApi();
    const { container } = render(<App />);

    expect(await screen.findByRole("heading", { name: /Entenda cada compra/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Início/, current: "page" })).toHaveAttribute("href", "/");
    const art = container.querySelector<HTMLImageElement>(".home-hero__art");
    expect(art?.src).toMatch(/^https:\/\/ddragon\.leagueoflegends\.com\/cdn\/img\/champion\/splash\/[A-Za-z]+_\d+\.jpg$/);
    expect(art).toHaveAttribute("alt", "");
    // O crédito só aparece quando a arte carrega; se falhar, a vitrine fica no fundo neutro.
    expect(container.querySelector(".home-hero__credit")).toBeNull();
    fireEvent.load(art!);
    expect(container.querySelector(".home-hero__credit")).toHaveTextContent("© Riot Games");
    fireEvent.error(container.querySelector(".home-hero__art")!);
    expect(container.querySelector(".home-hero__art")).toBeNull();
  });

  it("abre o Painel em /painel sem a faixa DEMO quando não há dados de demonstração", async () => {
    window.history.replaceState(null, "", "/painel");
    stubApi((api) => {
      api.on("GET", /^\/api\/v1\/dashboard$/, () => jsonResponse(503, {}));
      api.on("GET", /^\/api\/v1\/ranked\/me$/, () => jsonResponse(503, {}));
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Entenda a partida. Evolua a decisão." })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Painel/, current: "page" })).toHaveAttribute("href", "/painel");
    expect(screen.queryByText(/Partidas DEMO são sintéticas/)).not.toBeInTheDocument();
    // CC-1202: sem perfil da conta (erro), o topo mostra só o selo do servidor.
    expect(document.querySelector(".profile-chip__server")).toHaveTextContent("Servidor BR1");
    expect(screen.queryByRole("link", { name: /Seu perfil/ })).not.toBeInTheDocument();
  });
});

describe("App: moldura (CC-1202)", () => {
  const rank = (queue: string, state: string, tier: string | null, division: string | null, leaguePoints: number | null) => ({
    queue, state, tier, division, leaguePoints, wins: null, losses: null, observedAt: "2026-10-20T00:00:00Z",
  });

  it("chip do perfil da conta com ícone, Riot ID e melhor elo; faixa com patch e Data Dragon", async () => {
    stubApi((api) => {
      api.on("GET", /^\/api\/v1\/dashboard$/, () => jsonResponse(503, {}));
      api.on("GET", /^\/api\/v1\/ranked\/me$/, () => jsonResponse(503, {}));
      api.on("GET", /^\/api\/v1\/players\/me$/, () => jsonResponse(200, { reason: null, profile: {
        id: "00000000-0000-4000-8000-000000000001", gameName: "Conta Sintética", tagLine: "BR1",
        profileIconId: 29, summonerLevel: 120, refreshedAt: "2026-10-20T00:00:00Z", dataDragonVersion: "16.20.1",
        solo: rank("RANKED_SOLO_5x5", "UNRANKED", null, null, null),
        flex: rank("RANKED_FLEX_SR", "READY", "GOLD", "III", 76),
        staleReason: null,
      } }));
      api.on("GET", /^\/api\/v1\/catalog\/current$/, () => jsonResponse(200, { gamePatch: "16.20", dataDragonVersion: "16.20.1" }));
    });

    const { container } = render(<App />);

    const chip = await screen.findByRole("link", { name: "Seu perfil: Conta Sintética#BR1, Ouro III · 76 PDL" });
    expect(chip).toHaveAttribute("href", "/jogador/Conta%20Sint%C3%A9tica-BR1");
    expect(chip.querySelector("img")).toHaveAttribute("src", "https://ddragon.leagueoflegends.com/cdn/16.20.1/img/profileicon/29.png");
    expect(container.querySelector(".profile-chip__rank")).toHaveAttribute("data-tier", "GOLD");
    expect(await screen.findByText("Patch 16.20 · Data Dragon 16.20.1")).toBeInTheDocument();
    expect(screen.getByText(/Servidor BR1 · revisão de partidas concluídas/)).toBeInTheDocument();
  });
});
