import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMockApi, json, type MockApi } from "../../test/mockApi";
import { CommitmentNote, LateExplanation, LateMarks, LateSummary } from "./LateInsight";

const ANALYSIS = "33333333-3333-4333-8333-333333333333";
const LATE = {
  version: "late_analysis_v1",
  ordinal: 5,
  defaultItemId: 3031,
  deviationItemIds: [3033],
  gatePassed: true,
  claimStrong: true,
  sourcePatch: "16.19",
  plausibleCandidates: 30,
  championSupport: 214,
};

let mock: MockApi;
beforeEach(() => {
  mock = createMockApi();
  vi.stubGlobal("fetch", mock.fetch);
});
afterEach(() => vi.unstubAllGlobals());

describe("LateInsight (CC-1504/1505)", () => {
  it("marca o padrão e o desvio com as duas probabilidades", () => {
    const base = { rank: 1, score: 0.41, remainingCost: 3000, affordability: "UNKNOWN", evidenceCodes: [], tags: [], explanations: [] };
    const { rerender } = render(
      <LateMarks late={LATE} candidate={{ ...base, itemId: 3033, probability: 0.41, baseProbability: 0.09, isDeviation: true, isDefault: false }} />,
    );
    expect(screen.getByText("esta partida pede")).toBeInTheDocument();
    expect(screen.getByText("41% nesta partida · 9% no padrão")).toBeInTheDocument();
    rerender(
      <LateMarks late={{ ...LATE, claimStrong: false }} candidate={{ ...base, itemId: 3033, probability: 0.41, baseProbability: 0.09, isDeviation: true, isDefault: false }} />,
    );
    expect(screen.getByText("vale considerar nesta partida")).toBeInTheDocument();
    rerender(
      <LateMarks late={{ ...LATE, gatePassed: false }} candidate={{ ...base, itemId: 3031, probability: 0.5, baseProbability: 0.5, isDefault: true, isDeviation: false }} />,
    );
    expect(screen.getByText("padrão do campeão")).toBeInTheDocument();
    expect(screen.getByText("50% no padrão")).toBeInTheDocument();
  });

  it("resume a etapa e diz quando não há modelo aprovado", () => {
    render(<LateSummary late={{ ...LATE, gatePassed: false }} />);
    expect(screen.getByTestId("late-summary")).toHaveTextContent("5º item principal");
    expect(screen.getByTestId("late-summary")).toHaveTextContent("sem modelo contextual aprovado");
  });

  it("explica com as fontes e aceita uma pergunta", async () => {
    const answer = {
      analysisId: ANALYSIS,
      version: "explanation_v1",
      provider: "RULES",
      status: "DETERMINISTIC",
      sentences: [{ text: "O time inimigo tem 3 campeões com cura no kit.", sources: ["trigger:3033:m"] }],
      facts: [{ id: "trigger:3033:m", kind: "TRIGGER", title: "Gatilho de Lembrete Mortal", text: "O time inimigo tem 3 campeões com cura no kit." }],
      reasonCodes: ["MODEL_DISABLED"],
    };
    mock.on("POST", /\/explanation$/, () => json(200, answer));
    render(<LateExplanation analysisId={ANALYSIS} />);
    fireEvent.click(screen.getByRole("button", { name: "Explicar com as fontes" }));
    expect(await screen.findByText("(Gatilho de Lembrete Mortal)")).toBeInTheDocument();
    expect(screen.getByText("Texto montado diretamente dos fatos abaixo.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Pergunte sobre esta decisão"), { target: { value: "por que não armadura?" } });
    fireEvent.click(screen.getByRole("button", { name: "Perguntar" }));
    await waitFor(() => expect(mock.calls.filter((c) => c.path.endsWith("/explanation"))).toHaveLength(2));
    expect(mock.calls.at(-1)?.body).toEqual({ question: "por que não armadura?" });
  });

  it("depois de revelar, diz quando a escolha foi feita e quanto já estava pago", () => {
    render(
      <CommitmentNote
        commitment={{ kind: "FORCED", sharePaid: 0.68, anchorTimestampMs: 680000, completedAtMs: 845000, pathStartItemName: "Adaga Serrilhada", pathStartTimestampMs: 700000 }}
      />,
    );
    const note = screen.getByTestId("commitment-note");
    expect(note).toHaveTextContent("A decisão começa em 11:20; o caminho começou com Adaga Serrilhada em 11:40 e o item ficou pronto em 14:05.");
    expect(note).toHaveTextContent("68% do preço já estava pago");
    expect(note).toHaveTextContent("a compra já estava decidida");
  });
});
