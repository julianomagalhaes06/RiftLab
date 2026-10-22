import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../app/App";
import { createMockApi, json, problem } from "../../test/mockApi";
import { elapsed, queueName, spellIcon, styleIcon } from "./liveAssets";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.history.replaceState(null, "", "/");
});

const LANES = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"];

function player(n: number, overrides: Record<string, unknown> = {}) {
  return {
    position: LANES[n % 5],
    gameName: `Jogador ${n}`, tagLine: "BR1", bot: false, requested: n === 0, championId: 51 + n,
    champion: null, spell1Id: 4, spell2Id: 14, keystoneId: 8010, perkStyle: 8000, perkSubStyle: 8400,
    rankState: "READY", rank: { tier: "GOLD", division: "II", leaguePoints: 41, wins: 10, losses: 8 },
    ...overrides,
  };
}

export function liveGame(overrides: Record<string, unknown> = {}) {
  return {
    state: "IN_GAME", gameName: "Pão de Queijo", tagLine: "BR1", checkedAt: "2026-10-22T15:00:00Z",
    game: {
      queueId: 440, rankQueue: "RANKED_FLEX_SR", gameMode: "CLASSIC", mapId: 11, positionsEstimated: true,
      startedAt: new Date(Date.now() - 754_000).toISOString(), lengthSeconds: 754, dataDragonVersion: "16.20.1",
      teams: [
        { teamId: 100, bans: [{ championId: 99, champion: null }], participants: [player(0), player(1, { rankState: "UNRANKED", rank: null }), player(2), player(3), player(4)] },
        { teamId: 200, bans: [], participants: [player(5), player(6, { bot: true, gameName: null, tagLine: null, rankState: "UNAVAILABLE", rank: null }), player(7), player(8), player(9)] },
      ],
    },
    ...overrides,
  };
}

function setup(path: string, live: () => Response) {
  window.history.replaceState(null, "", path);
  const api = createMockApi();
  api.on("POST", /^\/api\/v1\/players\/live$/, live);
  vi.stubGlobal("fetch", api.fetch);
  render(<App />);
  return api;
}

describe("partida ao vivo (CC-1211)", () => {
  it("mostra os dois times com campeão, Riot ID, feitiços e elo, e marca o jogador buscado", async () => {
    const api = setup("/jogador/P%C3%A3o%20de%20Queijo-BR1/ao-vivo", () => json(200, liveGame()));
    const blue = await screen.findByRole("region", { name: "Time azul" });
    const red = screen.getByRole("region", { name: "Time vermelho" });
    expect(within(blue).getAllByRole("listitem")).toHaveLength(5);
    expect(within(red).getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByRole("heading", { name: "Pão de Queijo #BR1" })).toBeInTheDocument();
    expect(screen.getByText("Ranqueada Flex")).toBeInTheDocument();
    expect(screen.getByText(/12:3\d de jogo/)).toBeInTheDocument();
    expect(within(blue).getByRole("link", { name: "Jogador 1#BR1" })).toHaveAttribute("href", "/jogador/Jogador%201-BR1");
    expect(within(blue).getByText("(jogador buscado)", { exact: false })).toBeInTheDocument();
    expect(within(blue).getAllByText("Ouro II · 41 PDL")).toHaveLength(4);
    expect(within(blue).getByText("Sem classificação")).toBeInTheDocument();
    expect(within(red).getAllByText("Bot").length).toBeGreaterThan(0);
    expect(within(blue).getAllByAltText("Flash")[0]).toHaveAttribute("src", "https://ddragon.leagueoflegends.com/cdn/16.20.1/img/spell/SummonerFlash.png");
    expect(within(blue).getByText("Banimentos")).toBeInTheDocument();
    // CC-1214: rota estimada junto do campeão, na ordem topo → suporte, com a nota.
    expect(within(blue).getAllByText(/· (Topo|Selva|Meio|Atirador|Suporte)/).map((el) => el.textContent?.replace(/^ · /, ""))).toEqual(["Topo", "Selva", "Meio", "Atirador", "Suporte"]);
    expect(screen.getByText(/Rotas estimadas pelo campeão e pelos feitiços/)).toBeInTheDocument();
    // O Riot ID vai no corpo, fora da URL e dos logs de acesso.
    expect(api.callsTo("POST", /players\/live/)[0].body).toEqual({ gameName: "Pão de Queijo", tagLine: "BR1" });
  });

  it("fora de partida explica e verifica de novo; erro da Riot vira mensagem", async () => {
    let calls = 0;
    setup("/jogador/Fulano-BR1/ao-vivo", () => (++calls === 1
      ? json(200, { state: "NOT_IN_GAME", gameName: "Fulano", tagLine: "BR1", checkedAt: "2026-10-22T15:00:00Z", game: null })
      : problem(503, "RIOT_RATE_LIMITED")));
    expect(await screen.findByText("Fulano#BR1 não está em partida agora.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Verificar de novo" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Muitas consultas à Riot agora");
    await waitFor(() => expect(calls).toBe(2));
  });

  it("página do jogador leva à partida ao vivo", async () => {
    window.history.replaceState(null, "", "/jogador/Fulano-BR1");
    const api = createMockApi();
    api.on("POST", /^\/api\/v1\/players\/lookup$/, () => json(200, {
      id: "7a5c3f1e-0000-4000-8000-000000000001", gameName: "Fulano", tagLine: "BR1", profileIconId: null, summonerLevel: 30,
      refreshedAt: "2026-10-22T15:00:00Z", dataDragonVersion: null, solo: null, flex: null, staleReason: null,
    }));
    api.on("GET", /^\/api\/v1\/matches$/, () => json(200, { items: [], nextCursor: null }));
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);
    expect(await screen.findByRole("link", { name: "Partida ao vivo" })).toHaveAttribute("href", "/jogador/Fulano-BR1/ao-vivo");
    // Sem resposta do aviso (erro), o botão fica neutro e sem texto de "jogando agora".
    expect(screen.queryByText(/em partida neste momento/)).not.toBeInTheDocument();
  });

  it("aviso do op.gg (CC-1213): em partida, o botão acende com fila e tempo de jogo", async () => {
    window.history.replaceState(null, "", "/jogador/Fulano-BR1");
    const api = createMockApi();
    api.on("POST", /^\/api\/v1\/players\/lookup$/, () => json(200, {
      id: "7a5c3f1e-0000-4000-8000-000000000001", gameName: "Fulano", tagLine: "BR1", profileIconId: null, summonerLevel: 30,
      refreshedAt: "2026-10-22T15:00:00Z", dataDragonVersion: null, solo: null, flex: null, staleReason: null,
    }));
    api.on("POST", /^\/api\/v1\/players\/live\/status$/, () => json(200, {
      state: "IN_GAME", gameName: "Fulano", tagLine: "BR1", checkedAt: "2026-10-22T15:00:00Z",
      queueId: 420, gameMode: "CLASSIC", startedAt: new Date(Date.now() - 425_000).toISOString(),
    }));
    api.on("GET", /^\/api\/v1\/matches$/, () => json(200, { items: [], nextCursor: null }));
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);
    const button = await screen.findByRole("link", { name: "Ao vivo agora" });
    expect(button).toHaveAttribute("href", "/jogador/Fulano-BR1/ao-vivo");
    expect(screen.getByText(/Este jogador está em partida neste momento · Ranqueada Solo\/Duo · 7:0\d de jogo/)).toBeInTheDocument();
    expect(api.callsTo("POST", /live\/status/)[0].body).toEqual({ gameName: "Fulano", tagLine: "BR1" });
  });
});

describe("ícones e textos da partida ao vivo", () => {
  it("feitiços, estilos e filas conhecidos; desconhecidos sem ícone", () => {
    expect(spellIcon(14, "16.20.1")).toEqual({ name: "Incendiar", src: "https://ddragon.leagueoflegends.com/cdn/16.20.1/img/spell/SummonerDot.png" });
    expect(spellIcon(999, "16.20.1")).toEqual({ name: "Feitiço 999", src: null });
    expect(styleIcon(8400)?.name).toBe("Determinação");
    expect(styleIcon(1)).toBeNull();
    expect(queueName(420, "CLASSIC")).toBe("Ranqueada Solo/Duo");
    expect(queueName(null, "ARAM")).toBe("ARAM");
    expect(elapsed(null, 0)).toBeNull();
    expect(elapsed("2026-10-22T15:00:00Z", Date.parse("2026-10-22T15:12:05Z"))).toBe("12:05");
  });
});
