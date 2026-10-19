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
    expect(api.callsTo("GET", /^\/api\/v1\/matches$/)[0].search.get("scope")).toBe("account");
    expect(screen.getByText("1 RIOT · 1 DEMO")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Contexto da partida BR1_123" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "kai" } });
    expect(screen.getByRole("link", { name: "Contexto da partida BR1_123" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Contexto da partida DEMO-0008" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "br1_12" } });
    expect(screen.getByRole("link", { name: "Contexto da partida BR1_123" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "DEMO" }));
    expect(await screen.findByText("Nenhuma partida neste filtro")).toBeInTheDocument();
    expect(api.callsTo("GET", /^\/api\/v1\/matches$/).at(-1)?.search.get("scope")).toBe("demo");
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
    expect(screen.getByRole("link", { name: "Contexto da partida DEMO-0008" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Minha conta e DEMO" }));
    fireEvent.click(await screen.findByRole("button", { name: "Carregar mais partidas" }));
    expect(await screen.findByRole("link", { name: "Contexto da partida BR1_456" })).toBeInTheDocument();
    expect(screen.getByText("3 partidas carregadas")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Vitórias" })).not.toBeInTheDocument();
    expect(screen.getByText(/Filtro de resultado indisponível/)).toBeInTheDocument();
    expect(screen.queryByText("BR1_123")).not.toBeInTheDocument(); // o código não é mais o título do cartão
  });

  it("mostra resultado, KDA e times da conta local e filtra por resultado e fila", async () => {
    atHistory();
    const team = (prefix: number, win: boolean, local: { kills: number; deaths: number; assists: number } | null) =>
      [1, 2, 3, 4, 5].map((n) => ({
        participantId: prefix + n,
        championId: 900 + prefix + n,
        team: prefix ? 200 : 100,
        role: ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"][n - 1],
        win,
        kills: n === 4 && local ? local.kills : 3,
        deaths: n === 4 && local ? local.deaths : 1,
        assists: n === 4 && local ? local.assists : 1,
        totalMinionsKilled: n === 4 ? 200 : 20,
        neutralMinionsKilled: n === 4 ? 10 : 0,
        champion: { name: n === 4 && !prefix ? "Smolder" : `Campeão ${prefix + n}`, dataDragonVersion: "16.20.1", imageFile: "X.png" },
      }));
    const win = matchSummary({
      matchId: "BR1_1", queue: 440, patch: "16.20", durationSeconds: 1800, localParticipantId: 4,
      participants: [...team(0, true, { kills: 7, deaths: 4, assists: 6 }), ...team(5, false, null)],
    });
    const loss = matchSummary({
      matchId: "BR1_2", queue: 420, patch: "16.20", durationSeconds: 1500, localParticipantId: 4,
      participants: [...team(0, false, { kills: 2, deaths: 6, assists: 2 }), ...team(5, true, null)],
    });
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/matches$/, () => json(200, { items: [win, loss], nextCursor: null }));
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);

    const overview = await screen.findByRole("region", { name: "Resumo das suas partidas carregadas" });
    expect(within(overview).getByText("1V")).toBeInTheDocument();
    expect(within(overview).getByText("1D")).toBeInTheDocument();
    const winCard = screen.getByRole("article", { name: /^Smolder — Ranqueada Flex/ });
    expect(within(winCard).getByText("Vitória")).toBeInTheDocument();
    expect(within(winCard).getByLabelText("7 abates, 4 mortes, 6 assistências")).toBeInTheDocument();
    expect(within(winCard).getByText("3,25 KDA")).toBeInTheDocument();
    expect(within(winCard).getByText("68%")).toBeInTheDocument(); // (7 + 6) ÷ (7 + 4 × 3) abates do time
    expect(within(winCard).getByRole("list", { name: "Time vermelho" })).toBeInTheDocument();
    expect(within(winCard).getByRole("link", { name: "Contexto da partida BR1_1" })).toHaveAttribute("href", "/historico/BR1_1?participant=4");

    fireEvent.click(screen.getByRole("button", { name: "Derrotas" }));
    expect(screen.queryByRole("article", { name: /Ranqueada Flex/ })).not.toBeInTheDocument();
    expect(screen.getByRole("article", { name: /Ranqueada Solo\/Duo/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Todos" }));
    fireEvent.click(screen.getByRole("button", { name: "Flex" }));
    expect(screen.getByRole("article", { name: /Ranqueada Flex/ })).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: /Ranqueada Solo\/Duo/ })).not.toBeInTheDocument();
    expect(api.callsTo("GET", /^\/api\/v1\/matches\/[^/]+$/)).toHaveLength(0);
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
    expect(screen.getByRole("link", { name: "Importar ou abrir demonstração" })).toHaveAttribute("href", "/importar");
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
    fireEvent.click(await screen.findByRole("button", { name: "DEMO" }));
    fireEvent.click(await screen.findByRole("link", { name: "Contexto da partida DEMO-0008" }));
    expect(window.location.pathname).toBe("/historico/DEMO-0008");
    fireEvent.click(await screen.findByRole("link", { name: /Participante 1/ }));
    await waitFor(() => expect(window.location.search).toBe("?participant=1"));
    expect(screen.getByText(/Nenhuma decisão/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Voltar ao Histórico" })).toHaveAttribute("href", "/historico");
  });
});
