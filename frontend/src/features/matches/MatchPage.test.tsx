import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../app/App";
import { createMockApi, decisionSummary, json, matchDetail } from "../../test/mockApi";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const participants = [1, 2, 6, 7].map((participantId) => ({
  participantId,
  championId: 9000 + participantId,
  team: participantId < 6 ? 100 : 200,
  role: ["TOP", "BOTTOM"][participantId % 2],
  win: participantId < 6,
  kills: participantId,
  deaths: 2,
  assists: 3,
  totalMinionsKilled: 150,
  neutralMinionsKilled: 10,
  champion: null,
}));

const timeline = {
  matchId: "BR1_1",
  dataDragonVersion: null,
  participants: participants.map((p) => ({
    participantId: p.participantId,
    items: p.participantId === 2
      ? [
          { timestampMs: 5_000, itemId: 1055, kind: "PURCHASE", itemClass: "BASIC_OR_STARTER" },
          { timestampMs: 472_000, itemId: 3031, kind: "PURCHASE", itemClass: "MAIN" },
          { timestampMs: 900_000, itemId: 1055, kind: "SOLD", itemClass: "BASIC_OR_STARTER" },
        ]
      : [],
    skills: p.participantId === 2 ? [1, 2, 3, 1, 1, 4, 1, 1].map((skillSlot, i) => ({ timestampMs: i * 60_000, skillSlot, levelUpType: "NORMAL" })) : [],
  })),
  kills: [],
  objectives: [{ timestampMs: 600_000, kind: "DRAGON", team: 100, subType: "FIRE_DRAGON" }],
  goldFrames: [0, 1, 2].map((minute) => ({ timestampMs: minute * 60_000, gold: [500 + minute * 900, 500 + minute * 800, null, null, null, 500, 500 + minute * 600] })),
};

describe("tela da partida", () => {
  it("mostra resultado, placar, compras por visita à loja, habilidades e ouro", async () => {
    window.history.replaceState(null, "", "/historico/BR1_1");
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/matches\/BR1_1$/, () =>
      json(200, {
        ...matchDetail(), matchId: "BR1_1", queue: 440, patch: "16.20", supportLevel: "LIMITED", supportReason: "PATCH_NOT_VALIDATED",
        participants: participants.map((participant) => participant.participantId === 1
          ? { ...participant, finalItems: [100001, 100001], finalTrinketItemId: null }
          : participant.participantId === 2
            ? { ...participant, finalItems: [], finalTrinketItemId: 3340 }
            : participant.participantId === 6
              ? { ...participant, gameName: "Rival Ágil", tagLine: "BR1" }
              : participant),
        localParticipantId: 2,
      }),
    );
    api.on("GET", /^\/api\/v1\/matches\/BR1_1\/timeline$/, () => json(200, timeline));
    api.on("GET", /^\/api\/v1\/matches\/BR1_1\/decisions$/, () =>
      json(200, { items: [decisionSummary({ participantId: 2, timestampMs: 472_000, eligible: false, reasonCodes: ["OUT_OF_SCOPE"] })], nextCursor: null }),
    );
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);

    expect(await screen.findByRole("heading", { name: /^Partida BR1_1:\s*Vitória\s*·\s*Ranqueada Flex$/ })).toBeInTheDocument();
    expect(screen.getByText("Patch sem avaliação própria")).toHaveClass("chip");
    // CC-1206: cabeçalho como superfície com barra de resultado, sem a borda antiga .section.
    const hero = document.querySelector(".match-hero");
    expect(hero).toHaveClass("surface");
    expect(hero).toHaveAttribute("data-result", "win");
    expect(document.querySelector("section.section")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent(/Partida pronta/);

    const self = screen.getByRole("link", { name: "Participante 2 — Campeão #9002" });
    expect(self).toHaveAttribute("aria-current", "true"); // a conta local vem selecionada
    expect(within(self).getByText("Você")).toBeInTheDocument();
    // CC-1210: Riot ID da partida embaixo do campeão, com link para o perfil; sem nome, nada.
    // O link leva a partida e o participante: o perfil é achado pela conta (nome pode ter mudado).
    expect(screen.getByRole("link", { name: "Perfil de Rival Ágil#BR1" }).getAttribute("href")).toMatch(
      /^\/jogador\/Rival%20%C3%81gil-BR1\?partida=BR1_\d+&participante=\d+$/,
    );
    expect(screen.getAllByRole("link", { name: /^Perfil de / })).toHaveLength(1);
    expect(screen.getByText(/Riot ID como estava na partida/)).toBeInTheDocument();

    expect(await screen.findByText("A decisão de compra desta partida não pode ser analisada.")).toBeInTheDocument();
    const officialBuilds = await screen.findAllByRole("group", { name: "Inventário final oficial" });
    expect(officialBuilds).toHaveLength(2);
    expect(within(officialBuilds[0]).getByText("—")).toBeInTheDocument();
    expect(within(officialBuilds[1]).getAllByText("Item #100001, nome indisponível")).toHaveLength(2);
    expect(screen.getByRole("group", { name: "Amuleto final oficial" })).toBeInTheDocument();
    expect(await screen.findAllByRole("group", { name: "Estimado pela linha do tempo" })).not.toHaveLength(0);
    const path = await screen.findByRole("list", { name: "Compras por volta à loja" });
    expect(within(path).getAllByRole("listitem").filter((li) => li.classList.contains("build-path__visit"))).toHaveLength(3);
    expect(within(path).getByRole("link", { name: /^Decisão em 7:52/ })).toHaveTextContent("Bloqueada");
    expect(within(path).getByText(/Vendeu/)).toBeInTheDocument();
    expect(screen.getByText("Maximizou")).toBeInTheDocument();

    const chart = await screen.findByRole("img", { name: "Gráfico da diferença de ouro por minuto" });
    expect(chart).toBeInTheDocument();
    fireEvent.click(screen.getByText("Ver em tabela"));
    expect(screen.getByRole("cell", { name: "+1,1 mil" })).toBeInTheDocument(); // minuto 1: azul 2.700 − vermelho 1.600
    expect(screen.getByText("Remover esta partida do RiftLab")).toBeInTheDocument();
  });
});
