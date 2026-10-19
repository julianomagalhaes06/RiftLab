import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../app/App";
import { dashboard } from "../../test/dashboard";
import { createMockApi, json } from "../../test/mockApi";

function setup() {
  window.history.replaceState(null, "", "/painel");
  const api = createMockApi();
  api.on("GET", /^\/api\/v1\/ranked\/me$/, () => json(200, { status: "UNAVAILABLE", reason: "NO_OBSERVATION", position: null, change: null }));
  api.on("GET", /^\/api\/v1\/matches$/, () => json(200, { items: [], nextCursor: null }));
  vi.stubGlobal("fetch", api.fetch);
  return api;
}
afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

describe("Painel da conta própria", () => {
  it("apresenta denominadores separados e contexto do participante certo", async () => {
    const api = setup();
    api.on("GET", /^\/api\/v1\/dashboard$/, () => json(200, dashboard()));
    render(<App />);
    expect(await screen.findByText("57,14%")).toBeInTheDocument();
    expect(screen.getByText(/4 vitórias em 7 partidas com resultado de 8/)).toBeInTheDocument();
    expect(screen.getByText(/6 partidas com placar completo de 8/)).toBeInTheDocument();
    expect(screen.getByText("Tropas: 104 · duração 30:00")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Contexto da partida BR1_3090001" })).toHaveAttribute("href", "/historico/BR1_3090001?participant=4");
    expect(screen.getByText("Tentativas reveladas de 12 iniciadas")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Explorar campeão" })).toHaveAttribute("href", "/campeoes?champion=51");
    expect(screen.queryByRole("region", { name: "Partidas importadas recentes" })).not.toBeInTheDocument();
  });

  it("explica a fila automática e pede a fila escolhida no seletor (CC-904)", async () => {
    const api = setup();
    api.on("GET", /^\/api\/v1\/dashboard$/, ({ search }) =>
      search.get("queue") === "RANKED_SOLO_5x5"
        ? json(200, dashboard({ queue: 420, queueSelection: "EXPLICIT" }))
        : json(200, dashboard({ queue: 440, queueSelection: "AUTO", queues: [{ queue: 420, totalMatches: 10 }, { queue: 440, totalMatches: 8 }] })),
    );
    render(<App />);
    expect(await screen.findByText("Estatísticas descritivas da fila Flex, a da sua partida importada mais recente. Também há 10 partidas em Solo/Duo.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Fila"), { target: { value: "RANKED_SOLO_5x5" } });
    expect(await screen.findByText("Estatísticas descritivas da fila Solo/Duo.")).toBeInTheDocument();
    expect(api.callsTo("GET", /dashboard/).map((call) => call.search.get("queue"))).toEqual([null, "RANKED_SOLO_5x5"]);
  });

  it("esconde o recorte anterior enquanto troca a origem e não converte ausência em zero", async () => {
    const api = setup();
    let release!: (r: Response) => void;
    let calls = 0;
    api.on("GET", /^\/api\/v1\/dashboard$/, () => ++calls === 1 ? json(200, dashboard()) : new Promise<Response>((resolve) => { release = resolve; }));
    render(<App />);
    await screen.findByText("57,14%");
    fireEvent.change(screen.getByLabelText("Origem"), { target: { value: "DEMO" } });
    expect(screen.queryByText("57,14%")).not.toBeInTheDocument();
    await waitFor(() => expect(calls).toBe(2));
    release(json(200, dashboard({ origin: "DEMO", status: "EMPTY", totalMatches: 0, sampledMatches: 0, winSamples: 0, wins: 0, winRate: null, kdaSamples: 0, kda: null, distinctChampions: 0, champions: [], recentMatches: [] })));
    expect(await screen.findByText("Nenhuma partida neste recorte")).toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
    expect(api.callsTo("GET", /^\/api\/v1\/dashboard$/)[1].search.get("origin")).toBe("DEMO");
  });

  it("conta sem vínculo mantém estudo disponível sem atribuir desempenho", async () => {
    const api = setup();
    api.on("GET", /^\/api\/v1\/dashboard$/, () => json(200, dashboard({ status: "IDENTITY_UNAVAILABLE", totalMatches: 0, sampledMatches: 0, winSamples: 0, wins: 0, winRate: null, kdaSamples: 0, kda: null, distinctChampions: 0, champions: [], recentMatches: [] })));
    render(<App />);
    expect(await screen.findByText("Conta própria ainda sem vínculo observado")).toBeInTheDocument();
    const region = screen.getByRole("region", { name: "Seu recorte de partidas" });
    expect(within(region).queryByText("Taxa de vitória")).not.toBeInTheDocument();
    expect(within(region).getByText("Decisões revistas")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Partidas importadas recentes" })).toBeInTheDocument();
  });
});
