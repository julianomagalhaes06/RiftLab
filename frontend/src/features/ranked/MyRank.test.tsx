import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMockApi, json, type MockApi } from "../../test/mockApi";
import { MyRank } from "./MyRank";

function rank(queue = "RANKED_SOLO_5x5") {
  return {
    status: "READY", reason: null, change: null,
    position: { queue, season: "2026", observedAt: "2026-10-16T12:00:00Z", origin: "RIOT_LEAGUE_V4",
      state: "READY", tier: queue === "RANKED_SOLO_5x5" ? "GOLD" : "PLATINUM", division: "IV",
      leaguePoints: queue === "RANKED_SOLO_5x5" ? 20 : 75, wins: 10, losses: 5 },
  };
}

let mock: MockApi;
beforeEach(() => {
  mock = createMockApi();
  vi.stubGlobal("fetch", mock.fetch);
});
afterEach(() => vi.unstubAllGlobals());

describe("Minha posição por fila", () => {
  it("abre automaticamente na Flex quando só ela possui classificação", async () => {
    mock.on("GET", /^\/api\/v1\/ranked\/me$/, ({ search }) => json(200, search.get("queue") === "RANKED_FLEX_SR"
      ? rank("RANKED_FLEX_SR") : { ...rank(), status: "UNRANKED", position: { ...rank().position,
        state: "UNRANKED", tier: null, division: null, leaguePoints: null, wins: null, losses: null } }));
    render(<MyRank />);
    await screen.findByText("Elo Platina IV, 75 de 100 PDL");
    expect(screen.getByLabelText("Fila ranqueada")).toHaveValue("RANKED_FLEX_SR");
    expect(screen.getByText("Seleção automática · melhor classificação disponível")).toBeInTheDocument();
  });
  it.each([false, true])("separa Solo/Duo e Flex, inclusive no card compacto (%s)", async (compact) => {
    mock.on("GET", /^\/api\/v1\/ranked\/me$/, ({ search }) => json(200, rank(search.get("queue")!)));
    render(<MyRank compact={compact} />);
    await screen.findByText("Elo Platina IV, 75 de 100 PDL");
    expect(screen.getByLabelText("Fila ranqueada")).toHaveValue("RANKED_FLEX_SR");
    fireEvent.change(screen.getByLabelText("Fila ranqueada"), { target: { value: "RANKED_SOLO_5x5" } });
    await screen.findByText("Elo Ouro IV, 20 de 100 PDL");
    fireEvent.change(screen.getByLabelText("Fila ranqueada"), { target: { value: "RANKED_FLEX_SR" } });
    expect(screen.queryByText("Elo Ouro IV, 20 de 100 PDL")).not.toBeInTheDocument();
    await screen.findByText("Elo Platina IV, 75 de 100 PDL");
    expect(screen.getByText(/^Fila Flex · observado em/)).toBeInTheDocument();
    expect(mock.callsTo("GET", /^\/api\/v1\/ranked\/me$/).map((call) => call.search.get("queue")))
      .toEqual(["RANKED_SOLO_5x5", "RANKED_FLEX_SR"]);
  });

  it("não mostra elo Solo enquanto espera Flex e ignora respostas após a troca", async () => {
    let resolveSolo!: (value: Response) => void;
    mock.on("GET", /^\/api\/v1\/ranked\/me$/, ({ search }) => search.get("queue") === "RANKED_SOLO_5x5"
      ? new Promise<Response>((resolve) => { resolveSolo = resolve; })
      : json(200, rank("RANKED_FLEX_SR")));
    render(<MyRank />);
    fireEvent.change(screen.getByLabelText("Fila ranqueada"), { target: { value: "RANKED_FLEX_SR" } });
    await screen.findByText("Elo Platina IV, 75 de 100 PDL");
    await act(async () => {
      resolveSolo(json(200, { ...rank(), position: { ...rank().position, tier: "CHALLENGER", division: null, leaguePoints: 900 } }));
    });
    expect(screen.queryByText("Elo Ouro IV, 20 de 100 PDL")).not.toBeInTheDocument();
    expect(screen.getByText("Elo Platina IV, 75 de 100 PDL")).toBeInTheDocument();
    expect(screen.queryByText(/Elo Desafiante/)).not.toBeInTheDocument();
  });

  it("ausência de observação Flex não reaproveita elo Solo", async () => {
    mock.on("GET", /^\/api\/v1\/ranked\/me$/, ({ search }) => json(200, search.get("queue") === "RANKED_SOLO_5x5"
      ? rank() : { status: "UNAVAILABLE", reason: "NO_OBSERVATION", position: null, change: null }));
    render(<MyRank />);
    await screen.findByText("Elo Ouro IV, 20 de 100 PDL");
    fireEvent.change(screen.getByLabelText("Fila ranqueada"), { target: { value: "RANKED_FLEX_SR" } });
    await screen.findByText(/Ainda não há observação/);
    expect(screen.queryByText("Elo Ouro IV, 20 de 100 PDL")).not.toBeInTheDocument();
  });

  it("indica não classificado na Flex sem atribuir à Solo/Duo", async () => {
    mock.on("GET", /^\/api\/v1\/ranked\/me$/, () => json(200, {
      ...rank("RANKED_FLEX_SR"), status: "UNRANKED", position: { ...rank("RANKED_FLEX_SR").position,
        state: "UNRANKED", tier: null, division: null, leaguePoints: null, wins: null, losses: null },
    }));
    render(<MyRank />);
    fireEvent.change(screen.getByLabelText("Fila ranqueada"), { target: { value: "RANKED_FLEX_SR" } });
    await screen.findByText("Não classificado na Flex nesta observação válida.");
  });

  it("falha ao consultar Flex não exibe o elo Solo como se fosse Flex", async () => {
    mock.on("GET", /^\/api\/v1\/ranked\/me$/, ({ search }) => search.get("queue") === "RANKED_SOLO_5x5"
      ? json(200, rank()) : json(503, {}));
    render(<MyRank />);
    await screen.findByText("Elo Ouro IV, 20 de 100 PDL");
    fireEvent.change(screen.getByLabelText("Fila ranqueada"), { target: { value: "RANKED_FLEX_SR" } });
    await screen.findByRole("alert");
    expect(screen.queryByText("Elo Ouro IV, 20 de 100 PDL")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Fila ranqueada"), { target: { value: "RANKED_SOLO_5x5" } });
    await screen.findByText("Elo Ouro IV, 20 de 100 PDL");
  });
});

describe("card de elo (CC-1203)", () => {
  it("placas IV → I, barra de PDL, meta na divisão seguinte e ganho em verde", async () => {
    mock.on("GET", /^\/api\/v1\/ranked\/me$/, ({ search }) => json(200, search.get("queue") === "RANKED_SOLO_5x5"
      ? { ...rank(), position: { ...rank().position, division: "III", leaguePoints: 76 },
        change: { since: "2026-10-15T12:00:00Z", until: "2026-10-16T12:00:00Z", kind: "SAME_DIVISION", leaguePointsDelta: 22, ownMatchAssociation: "ONE" } }
      : { status: "UNAVAILABLE", reason: "NO_OBSERVATION", position: null, change: null }));
    const { container } = render(<MyRank compact />);
    await screen.findByText("Elo Ouro III, 76 de 100 PDL");
    expect(container.querySelector(".rank-card")).toHaveAttribute("data-tier", "GOLD");
    expect([...container.querySelectorAll(".rank-plate")].map((plate) => plate.className)).toEqual([
      "rank-plate rank-plate--done", "rank-plate rank-plate--now", "rank-plate rank-plate--next", "rank-plate rank-plate--next",
    ]);
    expect(screen.getByRole("img", { name: "76 de 100 pontos de liga na divisão" })).toBeInTheDocument();
    expect(container.querySelector(".rank-card__goal")).toHaveTextContent("Faltam 24 PDL para Ouro II");
    expect(screen.getByText(/Divisão III de IV a I; depois vem Platina/)).toBeInTheDocument();
    expect(container.querySelector(".rank-card__change--win")).toHaveTextContent("+22 PDL desde a observação anterior");
  });

  it("elo do topo troca a trilha por PDL acumulado; Diamante I aponta para Mestre", async () => {
    mock.on("GET", /^\/api\/v1\/ranked\/me$/, ({ search }) => json(200, search.get("queue") === "RANKED_SOLO_5x5"
      ? { ...rank(), position: { ...rank().position, tier: "MASTER", division: "I", leaguePoints: 312 } }
      : { ...rank("RANKED_FLEX_SR"), position: { ...rank("RANKED_FLEX_SR").position, tier: "DIAMOND", division: "I", leaguePoints: 90 } }));
    const { container } = render(<MyRank compact />);
    await screen.findByText("Elo Mestre, 312 PDL");
    expect(container.querySelector(".rank-card__plates")).toBeNull();
    expect(screen.getByText("Elos do topo não têm divisões.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Fila ranqueada"), { target: { value: "RANKED_FLEX_SR" } });
    await screen.findByText("Elo Diamante I, 90 de 100 PDL");
    expect(container.querySelector(".rank-card__goal")).toHaveTextContent("Faltam 10 PDL para Mestre");
  });
});

describe("Minha posição atualizada pela sincronização da conta", () => {
  const sync = { listed: 0, newEnqueued: 0, alreadyKnown: 0, skippedPurged: 0 };
  const unranked = () => ({ ...rank(), status: "STALE", reason: "OBSERVATION_OLD", position: { ...rank().position,
    state: "UNRANKED", tier: null, division: null, leaguePoints: null, wins: null, losses: null } });

  it("pede a sincronização ao abrir e relê a posição quando a execução termina", async () => {
    let refreshed = false;
    mock.on("POST", /^\/api\/v1\/history\/sync$/, () => json(202, { ...sync, state: "QUEUED", requestedAt: "2026-11-04T12:00:00Z" }));
    mock.on("GET", /^\/api\/v1\/history\/sync$/, () => {
      refreshed = true;
      return json(200, { ...sync, state: "SUCCEEDED", finishedAt: "2026-11-04T12:00:02Z", nextAllowedAt: "2099-01-01T00:00:00Z" });
    });
    mock.on("GET", /^\/api\/v1\/ranked\/me$/, ({ search }) => json(200, search.get("queue") === "RANKED_SOLO_5x5"
      ? (refreshed ? { ...rank(), position: { ...rank().position, tier: "SILVER", leaguePoints: 60 } } : unranked())
      : { ...unranked(), position: { ...unranked().position, queue: "RANKED_FLEX_SR" } }));
    render(<MyRank />);
    expect(await screen.findByText("Atualizando a posição com a Riot…")).toBeInTheDocument();
    // Durante a atualização, o aviso de observação antiga não contradiz o estado.
    expect(screen.queryByText(/Esta observação tem mais de 24 horas/)).not.toBeInTheDocument();
    expect(await screen.findByText("Elo Prata IV, 60 de 100 PDL", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.queryByText("Atualizando a posição com a Riot…")).not.toBeInTheDocument();
    expect(mock.callsTo("POST", /^\/api\/v1\/history\/sync$/)).toHaveLength(1);
    expect(mock.callsTo("GET", /^\/api\/v1\/ranked\/me$/)).toHaveLength(4);
  }, 10000);

  it("no intervalo mínimo ou sem chave, mantém a posição guardada sem nova leitura", async () => {
    mock.on("POST", /^\/api\/v1\/history\/sync$/, () => json(200, { ...sync, state: "SUCCEEDED", reason: "COOLDOWN",
      finishedAt: "2026-11-04T12:00:02Z", nextAllowedAt: "2099-01-01T00:00:00Z" }));
    mock.on("GET", /^\/api\/v1\/ranked\/me$/, ({ search }) => json(200, rank(search.get("queue")!)));
    render(<MyRank />);
    await screen.findByText("Elo Platina IV, 75 de 100 PDL");
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)); });
    expect(screen.queryByText("Atualizando a posição com a Riot…")).not.toBeInTheDocument();
    expect(mock.callsTo("GET", /^\/api\/v1\/history\/sync$/)).toHaveLength(0);
    expect(mock.callsTo("GET", /^\/api\/v1\/ranked\/me$/)).toHaveLength(2);
  });
});

describe("Minha posição com contas locais (Sprint 2, #13)", () => {
  it("orienta o vínculo quando o usuário ainda não tem conta Riot vinculada", async () => {
    mock.on("GET", /^\/api\/v1\/ranked\/me$/, () => json(200, { status: "UNAVAILABLE", reason: "ACCOUNT_NOT_LINKED", position: null, change: null }));
    render(<MyRank />);
    expect(await screen.findAllByText(/Vincule sua conta Riot em Minha conta/)).not.toHaveLength(0);
  });
});
