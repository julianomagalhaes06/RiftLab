import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMockApi, json } from "../../test/mockApi";
import { DecisionTrace } from "./DecisionTrace";

afterEach(() => vi.unstubAllGlobals());

const DECISION = "11111111-1111-4111-8111-111111111111";

function trace(overrides: Record<string, unknown> = {}) {
  return {
    decisionId: DECISION,
    matchId: "BR1_3091234567",
    participantId: 1,
    patch: "16.20",
    queue: 420,
    timelineEvents: 1384,
    participantItemEvents: 17,
    event: {
      sourceIndex: 412,
      timestampMs: 872_345,
      type: "ITEM_PURCHASED",
      itemId: null,
      level: null,
      hidden: ["itemId"],
      raw: { type: "ITEM_PURCHASED", timestamp: 872_345, participantId: 1 },
    },
    nearby: [
      { sourceIndex: 370, timestampMs: 760_120, type: "LEVEL_UP", itemId: null, level: 10, hidden: [], raw: { type: "LEVEL_UP", timestamp: 760_120, participantId: 1, level: 10 } },
      { sourceIndex: 388, timestampMs: 812_004, type: "ITEM_PURCHASED", itemId: 1036, level: null, hidden: [], raw: { type: "ITEM_PURCHASED", timestamp: 812_004, participantId: 1, itemId: 1036 } },
    ],
    frame: { timestampMs: 840_210, gold: 6450, level: 10, cs: 142, xp: 7210 },
    frameCount: 31,
    ...overrides,
  };
}

function mount(body: unknown, status = 200) {
  const api = createMockApi();
  api.on("GET", /^\/api\/v1\/decisions\/[^/]+\/trace$/, () => json(status, body));
  vi.stubGlobal("fetch", api.fetch);
  return render(<DecisionTrace decisionId={DECISION} />);
}

describe("Da Riot até esta análise", () => {
  it("mostra o evento oficial sem o item comprado, os eventos anteriores, o quadro e o inventário", async () => {
    mount(trace());
    expect(await screen.findByRole("heading", { name: "Da Riot até esta análise" })).toBeInTheDocument();
    expect(screen.getByText(/1\.384 eventos registrados pela Riot/)).toBeInTheDocument();
    const raw = screen.getByLabelText("Evento bruto da Riot");
    expect(raw).toHaveTextContent('"type": "ITEM_PURCHASED"');
    expect(raw).toHaveTextContent('"timestamp": 872345');
    expect(raw).toHaveTextContent('"itemId": /* oculto até a resposta */');
    expect(screen.getByText(/oculto até você responder/)).toBeInTheDocument();
    const events = within(screen.getByRole("heading", { name: "O que aconteceu nos 2 minutos antes" }).parentElement!).getAllByRole("listitem");
    expect(events[0]).toHaveTextContent("Subiu para o nível 10");
    expect(events[1]).toHaveTextContent("Comprou");
    expect(screen.getByText(/6\.450 de ouro acumulado · nível 10 · 142 tropas · 32 s antes da compra/)).toBeInTheDocument();
    expect(screen.getByText(/17 eventos de item deste jogador/)).toBeInTheDocument();
  });

  it("explica a ausência de quadros e de eventos e some se a rota falhar", async () => {
    const { unmount } = mount(trace({ frame: null, frameCount: 0, nearby: [] }));
    expect(await screen.findByText(/Os quadros desta partida ainda não foram lidos/)).toBeInTheDocument();
    expect(screen.getByText(/Nenhuma compra, venda ou subida de nível/)).toBeInTheDocument();
    unmount();
    const { container, unmount: close } = mount({ code: "INTERNAL_ERROR" }, 500);
    await vi.waitFor(() => expect(container.querySelector(".decision-trace")).toBeNull());
    close();
    // Resposta fora do formato (outro recurso) também esconde a seção sem quebrar a tela.
    const other = mount({ decisionId: DECISION, eligible: true });
    await vi.waitFor(() => expect(other.container.querySelector(".decision-trace")).toBeNull());
  });
});
