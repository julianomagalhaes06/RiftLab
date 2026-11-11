import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../app/App";
import { championPatterns } from "../../test/championPatterns";
import { createMockApi, json, problem } from "../../test/mockApi";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("Campeões com padrões observados", () => {
  it("mostra denominadores, preserva filtros na URL e abre o contexto sem revelar uma resposta", async () => {
    window.history.replaceState(null, "", "/campeoes?champion=51&phase=first&patch=16.19");
    const mock = createMockApi();
    mock.on("GET", /^\/api\/v1\/champions\/patterns$/, ({ search }) => json(200,
      championPatterns(search.get("championId") === "222" ? {
        championId: 222, status: "NO_ELIGIBLE", patterns: [], situations: [],
        totalParticipations: 0, sampledParticipations: 0, eligibleDecisions: 0,
        observedDecisions: 0, patternDenominator: 0,
      } : {})));
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);
    expect(await screen.findByText("Infinity Edge")).toBeInTheDocument();
    expect(screen.getByText("3 de 5")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Abrir contexto" })).toHaveAttribute("href", "/historico/BR1_123?participant=4");
    expect(screen.getByRole("link", { name: "Analisar decisão" })).toHaveAttribute("href", "/decisions/11111111-1111-4111-8111-111111111111");
    expect(screen.getByText(/Sem ranking de itens validado/)).toBeInTheDocument();
    // CC-904: campeão sem partida no filtro sai da lista e aparece numa linha.
    expect(screen.queryByRole("button", { name: /Miss Fortune/ })).not.toBeInTheDocument();
    expect(screen.getByText("Sem partidas no patch 16.19: Miss Fortune.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Jinx/ }));
    expect(await screen.findByText("Ainda não há trajetos elegíveis neste filtro")).toBeInTheDocument();
    // CC-1206: lista como "Campeões em foco"; só o escolhido tem véu e barra roxos.
    const jinx = screen.getByRole("button", { name: /Jinx/ });
    expect(jinx).toHaveAttribute("aria-pressed", "true");
    expect(jinx.closest("li")).toHaveClass("result-bar", "is-selected");
    expect(document.querySelectorAll(".champions-roster .is-selected")).toHaveLength(1);
    expect(screen.queryByText("Infinity Edge")).not.toBeInTheDocument();
    expect(window.location.search).toContain("champion=222");
    expect(window.location.search).toContain("phase=first");
    fireEvent.change(screen.getByLabelText("Patch"), { target: { value: "16.18" } });
    await waitFor(() => expect(window.location.search).toContain("patch=16.18"));
    expect(mock.calls.some((call) => call.search.get("patch") === "16.18")).toBe(true);
  });

  it("abre no patch atual do jogo, mostra as partidas coletadas nele e permite outro patch", async () => {
    window.history.replaceState(null, "", "/campeoes");
    const mock = createMockApi();
    mock.on("GET", /^\/api\/v1\/champions\/patterns$/, ({ search }) => json(200, search.get("patch") === "16.21"
      ? championPatterns({ patches: ["16.22", "16.21", "16.19", "16.18"], currentPatch: "16.22", patch: "16.21",
        status: "OUT_OF_SCOPE", patterns: [], situations: [] })
      : championPatterns({ patches: ["16.22", "16.21", "16.19", "16.18"], currentPatch: "16.22", patch: "16.22",
        status: "OUT_OF_SCOPE", patterns: [], situations: [], champions: championPatterns().champions.map(
          (champion) => ({ ...champion, matches: champion.championId === 51 ? 3 : 0 })) })));
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);
    expect(await screen.findByText("Partidas coletadas no patch 16.22 (atual)")).toBeInTheDocument();
    const select = screen.getByLabelText("Patch");
    expect(select).toHaveValue("16.22");
    expect(screen.getByRole("option", { name: "16.22 (atual)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "16.21" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Caitlyn/ })).toHaveTextContent("3 partidas");
    expect(screen.getByText("Sem partidas no patch 16.22: Jinx, Miss Fortune.")).toBeInTheDocument();
    expect(screen.getByText(/O patch 16.22 ainda não está na janela de análise \(16.19 e 16.18\)/)).toBeInTheDocument();
    // O pedido sem patch deixa o servidor escolher o atual.
    expect(mock.callsTo("GET", /^\/api\/v1\/champions\/patterns$/).map((call) => call.search.get("patch"))).toEqual([null]);
    // Estado vazio com saída: o botão leva ao patch mais novo da janela de análise.
    fireEvent.click(screen.getByRole("button", { name: "Ver no patch 16.19" }));
    await waitFor(() => expect(window.location.search).toContain("patch=16.19"));
    fireEvent.change(screen.getByLabelText("Patch"), { target: { value: "16.21" } });
    expect(await screen.findByText("Partidas coletadas no patch 16.21")).toBeInTheDocument();
    expect(window.location.search).toContain("patch=16.21");
  });

  it("abstém com amostra pequena e mantém as situações para estudo", async () => {
    window.history.replaceState(null, "", "/campeoes");
    const mock = createMockApi();
    mock.on("GET", /^\/api\/v1\/champions\/patterns$/, () => json(200,
      championPatterns({ status: "SMALL_SAMPLE", patterns: [], patternDenominator: 2 })));
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);
    expect(await screen.findByText("Amostra insuficiente para publicar padrões")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Trajetos observados" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Abrir contexto" })).toBeInTheDocument();
  });

  it("recupera falha de rede sem mostrar padrões antigos", async () => {
    window.history.replaceState(null, "", "/campeoes");
    const mock = createMockApi();
    let available = false;
    mock.on("GET", /^\/api\/v1\/champions\/patterns$/, () => available
      ? json(200, championPatterns()) : problem(503, "SERVICE_UNAVAILABLE"));
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Não foi possível carregar");
    available = true;
    fireEvent.click(screen.getByRole("button", { name: "Tentar novamente" }));
    expect(await screen.findByText("Infinity Edge")).toBeInTheDocument();
  });
});
