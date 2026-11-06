import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../app/App";
import { createMockApi, json, matchSummary } from "../../test/mockApi";
import { IN_APP_STATE } from "../../shared/router/intent";
import { readRecentPlayers, rememberPlayer } from "./recentPlayers";

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

/** `fromApp`: a entrada veio do roteador do RiftLab (busca, link interno); sem ela, o endereço foi
 * aberto de fora (link de outro site, favorito) e nada vai à Riot sem o clique (CC-1218). */
function setup(path: string, configure: (api: ReturnType<typeof createMockApi>) => void, fromApp = true) {
  window.history.replaceState(fromApp ? IN_APP_STATE : null, "", path);
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
    const search = await screen.findByRole("combobox", { name: "Buscar jogador pelo Riot ID" });
    // TAG digitada inválida: avisa sem sair da página.
    fireEvent.change(search, { target: { value: "Nome#X" } });
    fireEvent.submit(search);
    const form = screen.getByRole("search", { name: "Buscar jogador" });
    expect(await within(form).findByRole("alert")).toHaveTextContent("se quiser, a TAG: Nome#TAG");

    // Só o nome: Enter busca com a TAG padrão do servidor escolhido (BR → #BR1), como no op.gg.
    expect(within(form).getByRole("combobox", { name: "Servidor" })).toHaveValue("BR");
    fireEvent.change(search, { target: { value: "Pão de Queijo" } });
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

  it("endereço aberto de fora do RiftLab não consulta a Riot até o clique (CC-1218)", async () => {
    const api = setup("/jogador/Pão de Queijo-BR1", (mock) => {
      mock.on("POST", /^\/api\/v1\/players\/lookup$/, () => json(200, profile()));
      mock.on("POST", /\/players\/.+\/sync$/, () => json(202, { state: "QUEUED" }));
      mock.on("POST", /^\/api\/v1\/players\/live\/status$/, () => json(200, { state: "NOT_IN_GAME", gameName: "Pão de Queijo", tagLine: "BR1", checkedAt: "2026-10-19T15:00:00Z" }));
    }, false);
    const confirm = await screen.findByRole("button", { name: "Buscar Pão de Queijo#BR1 na Riot" });
    expect(screen.getByRole("heading", { name: /Pão de Queijo #BR1/ })).toBeInTheDocument();
    expect(screen.getByText(/aberto de fora do RiftLab/)).toBeInTheDocument();
    // Nem a busca, nem o aviso ao vivo, nem a sincronização: nada vai à Riot sem o clique.
    expect(api.callsTo("POST", /^\/api\/v1\/players\//)).toEqual([]);

    fireEvent.click(confirm);
    expect(await screen.findByText(/Nível 321/)).toBeInTheDocument();
    await vi.waitFor(() => {
      expect(api.callsTo("POST", /\/players\/lookup$/)).toHaveLength(1);
      expect(api.callsTo("POST", /\/players\/live\/status$/)).toHaveLength(1);
      expect(api.callsTo("POST", /\/players\/.+\/sync$/)).toHaveLength(1);
    });
    // A entrada passa a contar como do app: recarregar não pede o clique de novo.
    expect(window.history.state).toEqual(IN_APP_STATE);
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

  it("sugestões: parte do nome acha o perfil já aberto; setas e Enter abrem; Esc fecha (CC-1208)", async () => {
    rememberPlayer(profile() as never);
    rememberPlayer(profile({ gameName: "Outro Jogador", tagLine: "XYZ" }) as never);
    setup("/", (mock) => {
      mock.on("POST", /^\/api\/v1\/players\/lookup$/, () => json(200, profile()));
    });
    const search = await screen.findByRole("combobox", { name: "Buscar jogador pelo Riot ID" });
    fireEvent.focus(search);
    expect(search).toHaveAttribute("aria-expanded", "true");
    fireEvent.change(search, { target: { value: "queijo" } });
    const list = screen.getByRole("listbox", { name: "Sugestões de jogadores" });
    const options = within(list).getAllByRole("option");
    // Recente que contém o texto e, sem "#", o mesmo texto com a TAG BR1.
    expect(options.map((option) => option.textContent)).toEqual([
      "Pão de Queijo #BR1Nível 321 · Ouro II",
      "#Buscar queijo #BR1TAG padrão do servidor Brasil",
    ]);
    fireEvent.keyDown(search, { key: "ArrowDown" });
    expect(search).toHaveAttribute("aria-activedescendant", options[0].id);
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(search, { key: "Escape" });
    expect(search).toHaveAttribute("aria-expanded", "false");
    fireEvent.keyDown(search, { key: "ArrowDown" });
    fireEvent.submit(search);
    expect(window.location.pathname).toBe("/jogador/P%C3%A3o%20de%20Queijo-BR1");
    // No início a busca é a grande da vitrine; no perfil, a do topo, que abre vazia.
    expect(await screen.findByRole("combobox", { name: "Buscar jogador pelo Riot ID" })).toHaveValue("");
  });

  it("sugestão de TAG abre Nome#BR1; Limpar apaga as buscas recentes (CC-1208)", async () => {
    rememberPlayer(profile() as never);
    setup("/", (mock) => {
      mock.on("POST", /^\/api\/v1\/players\/lookup$/, () => json(200, profile({ gameName: "Novo Nome" })));
    });
    const search = await screen.findByRole("combobox", { name: "Buscar jogador pelo Riot ID" });
    fireEvent.focus(search);
    fireEvent.click(screen.getByRole("button", { name: "Limpar buscas recentes" }));
    expect(readRecentPlayers()).toEqual([]);
    expect(screen.queryByRole("option", { name: /Pão de Queijo/ })).not.toBeInTheDocument();
    fireEvent.change(search, { target: { value: "Novo Nome" } });
    fireEvent.keyDown(search, { key: "ArrowDown" });
    fireEvent.submit(search);
    expect(window.location.pathname).toBe("/jogador/Novo%20Nome-BR1");
    // Abrir o perfil o guarda de novo, com o nome que a Riot devolveu.
    await screen.findByText(/Nível 321/);
    expect(readRecentPlayers().map((player) => player.gameName)).toEqual(["Novo Nome"]);
  });
});
