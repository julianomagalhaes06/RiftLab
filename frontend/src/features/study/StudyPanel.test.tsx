import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMockApi, json, type MockApi } from "../../test/mockApi";
import { StudyPanel } from "./StudyPanel";

const ATTEMPT = "11111111-1111-4111-8111-111111111111";
const DECISION = "22222222-2222-4222-8222-222222222222";

function attempt(overrides: Record<string, unknown> = {}) {
  return {
    attemptId: ATTEMPT,
    decisionId: DECISION,
    status: "OPEN",
    objective: { priority: "SURVIVAL" },
    context: {},
    options: [
      { itemId: 3075, name: "Thornmail" },
      { itemId: 3031, name: "Infinity Edge" },
    ],
    hintLevel: 0,
    hints: [],
    reasonCodes: [],
    selectedItemId: null,
    skipped: false,
    rationale: null,
    analysisId: null,
    comparison: null,
    historicalItemId: null,
    ...overrides,
  };
}

const COMPARISON = {
  analysisId: "33333333-3333-4333-8333-333333333333",
  decisionId: DECISION,
  status: "AVAILABLE",
  engine: "POPULARITY",
  objective: { priority: "SURVIVAL" },
  versions: { model: null, catalog: "16.19/16.19.1", features: "f", candidates: "c", ranking: "popularity_16_19_v1", objective: "objective_v1", itemRules: "16.19.json" },
  quality: { budgetSource: "FRAME_ESTIMATE", frameAgeMs: 1000, warnings: [] },
  support: { level: "SUPPORTED", reasonCodes: [], legalCandidates: 90, referenceDecisions: 128 },
  candidates: [
    { itemId: 3075, name: "Thornmail", rank: 1, baseRank: 2, score: 1.2, remainingCost: 1200, affordability: "UNKNOWN", evidenceCodes: [], tags: ["Armor"], explanations: [{ text: "Faltam 1200 de ouro para completar o item.", source: "candidates.remainingCost" }] },
    { itemId: 3031, name: "Infinity Edge", rank: 2, baseRank: 1, score: 1.0, remainingCost: 2000, affordability: "UNKNOWN", evidenceCodes: [], tags: ["Damage"], explanations: [] },
  ],
  scoreNote: "Pontuações são relativas a esta decisão; não são probabilidades.",
};

let mock: MockApi;
beforeEach(() => {
  mock = createMockApi();
  vi.stubGlobal("fetch", mock.fetch);
  vi.stubGlobal("crypto", { randomUUID: () => "key-0000-test" });
});
afterEach(() => vi.unstubAllGlobals());

describe("StudyPanel (CC-304/305): escolha antes da revelação", () => {
  it("objetivo confirmado antes; nada de custo, posição ou histórico antes de revelar", async () => {
    mock.on("POST", /^\/api\/v1\/learning-attempts$/, () => json(201, attempt()));
    mock.on("POST", /\/hint$/, () =>
      json(200, attempt({ hintLevel: 2, hints: [{ level: 2, text: "Thornmail: faltam 1200 de ouro; atributos: Armor.", source: "candidates.remainingCost" }] })),
    );
    mock.on("POST", /\/answer$/, () => json(200, attempt({ status: "ANSWERED", selectedItemId: 3075 })));
    mock.on("POST", /\/reveal$/, () =>
      json(200, attempt({ status: "REVEALED", selectedItemId: 3075, analysisId: COMPARISON.analysisId, comparison: COMPARISON, historicalItemId: 3031 })),
    );
    render(<StudyPanel decisionId={DECISION} />);

    fireEvent.click(screen.getByLabelText(/Sobrevivência/));
    fireEvent.click(screen.getByRole("button", { name: /Confirmar objetivo/ }));
    await screen.findByText("Thornmail");
    const created = mock.callsTo("POST", /^\/api\/v1\/learning-attempts$/)[0];
    expect(created.body).toEqual({ decisionId: DECISION, objective: { priority: "SURVIVAL" }, mode: "STUDY" });
    expect(screen.queryByText(/1200/)).toBeNull();
    expect(screen.queryByText(/histórico/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /custo e atributos/ }));
    await screen.findByText(/faltam 1200 de ouro/);

    fireEvent.click(screen.getByLabelText("Thornmail"));
    fireEvent.click(screen.getByRole("button", { name: "Responder" }));
    await waitFor(() => expect(mock.callsTo("POST", /\/answer$/)).toHaveLength(1));
    expect(mock.callsTo("POST", /\/answer$/)[0].body).toEqual({ itemId: 3075, skipped: false, rationale: null });

    fireEvent.click(screen.getByRole("button", { name: /Revelar/ }));
    await screen.findByRole("heading", { name: "Comparação" });
    expect(screen.getByText(/— sua escolha/)).toBeInTheDocument();
    expect(screen.getByText(/compra feita na partida \(histórico\)/)).toBeInTheDocument();
    expect(screen.getByText(/subiu pela prioridade do objetivo/)).toBeInTheDocument();
    expect(screen.getByText(/fonte: candidates.remainingCost/)).toBeInTheDocument();
    expect(screen.getByText(/não são probabilidades/)).toBeInTheDocument();
  });

  it("modelo ativo recusado por hash aparece nas versões usadas", async () => {
    const refused = { ...COMPARISON, versions: { ...COMPARISON.versions, registryVersion: null, registryFallback: "ACTIVE_ARTIFACT_HASH_MISMATCH" } };
    mock.on("POST", /^\/api\/v1\/learning-attempts$/, () => json(201, attempt()));
    mock.on("POST", /\/reveal$/, () => json(200, attempt({ status: "REVEALED", analysisId: refused.analysisId, comparison: refused, historicalItemId: 3031 })));
    render(<StudyPanel decisionId={DECISION} />);
    fireEvent.click(screen.getByRole("button", { name: /Confirmar objetivo/ }));
    await screen.findByText("Thornmail");
    fireEvent.click(screen.getByRole("button", { name: /Revelar/ }));
    await screen.findByRole("heading", { name: "Comparação" });
    expect(screen.getByText(/versão ativa foi recusada porque o arquivo não confere com o hash/)).toBeInTheDocument();
  });

  it("sem alternativas mostra os motivos em vez de tela vazia", async () => {
    mock.on("POST", /^\/api\/v1\/learning-attempts$/, () =>
      json(201, attempt({ options: [], reasonCodes: ["PATCH_UNSUPPORTED"] })),
    );
    render(<StudyPanel decisionId={DECISION} />);
    fireEvent.click(screen.getByRole("button", { name: /Confirmar objetivo/ }));
    await screen.findByText(/Não há alternativas para estudar/);
    expect(screen.getByText(/Não há regras de itens para o patch/)).toBeInTheDocument();
  });

  it("menos de três alternativas é dito explicitamente na comparação", async () => {
    const one = { ...COMPARISON, candidates: [COMPARISON.candidates[0]] };
    mock.on("POST", /^\/api\/v1\/learning-attempts$/, () => json(201, attempt()));
    mock.on("POST", /\/reveal$/, () => json(200, attempt({ status: "REVEALED", analysisId: one.analysisId, comparison: one, historicalItemId: 3075 })));
    render(<StudyPanel decisionId={DECISION} />);
    fireEvent.click(screen.getByRole("button", { name: /Confirmar objetivo/ }));
    await screen.findByText("Thornmail");
    fireEvent.click(screen.getByRole("button", { name: /Revelar/ }));
    await screen.findByText(/Só 1 alternativa legal/);
  });

  it("cenário com ouro informado é separado e rotulado como cenário", async () => {
    mock.on("POST", /^\/api\/v1\/learning-attempts$/, () => json(201, attempt()));
    mock.on("POST", /\/reveal$/, () => json(200, attempt({ status: "REVEALED", analysisId: COMPARISON.analysisId, comparison: COMPARISON, historicalItemId: 3031 })));
    mock.on("POST", /^\/api\/v1\/analyses$/, () =>
      json(201, { ...COMPARISON, candidates: [{ ...COMPARISON.candidates[0], affordability: "WITHIN_USER_BUDGET" }] }),
    );
    render(<StudyPanel decisionId={DECISION} />);
    fireEvent.click(screen.getByRole("button", { name: /Confirmar objetivo/ }));
    await screen.findByText("Thornmail");
    fireEvent.click(screen.getByRole("button", { name: /Revelar/ }));
    fireEvent.change(await screen.findByLabelText("Ouro"), { target: { value: "1500" } });
    fireEvent.click(screen.getByRole("button", { name: "Simular cenário" }));
    await screen.findByText(/Cenário com 1500 de ouro/);
    expect(screen.getByText(/cabe no ouro informado/)).toBeInTheDocument();
    expect(mock.callsTo("POST", /^\/api\/v1\/analyses$/)[0].body).toEqual({
      decisionId: DECISION,
      objective: { priority: "SURVIVAL" },
      budget: { gold: 1500, source: "USER_PROVIDED" },
    });
  });
  it("reanalisar com objetivo de texto confirmado cria análise separada e mantém a comparação", async () => {
    mock.on("POST", /^\/api\/v1\/learning-attempts$/, () => json(201, attempt()));
    mock.on("POST", /\/reveal$/, () => json(200, attempt({ status: "REVEALED", analysisId: COMPARISON.analysisId, comparison: COMPARISON, historicalItemId: 3031 })));
    mock.on("POST", /^\/api\/v1\/objectives\/interpret$/, () =>
      json(200, { status: "INTERPRETED", objective: { priority: "DAMAGE", parserVersion: "rules_v1" }, parserVersion: "rules_v1", provider: "RULES", reasonCodes: [] }),
    );
    mock.on("POST", /^\/api\/v1\/analyses$/, () =>
      json(201, { ...COMPARISON, objective: { priority: "DAMAGE", parserVersion: "rules_v1" }, candidates: [{ ...COMPARISON.candidates[1], rank: 1 }, { ...COMPARISON.candidates[0], rank: 2 }] }),
    );
    render(<StudyPanel decisionId={DECISION} />);
    fireEvent.click(screen.getByRole("button", { name: /Confirmar objetivo/ }));
    await screen.findByText("Thornmail");
    fireEvent.click(screen.getByRole("button", { name: /Revelar/ }));
    await screen.findByRole("heading", { name: "Reanalisar com outro objetivo" });
    fireEvent.change(screen.getByLabelText(/descreva em uma frase/), { target: { value: "quero bater mais" } });
    fireEvent.click(screen.getByRole("button", { name: "Interpretar texto" }));
    fireEvent.click(await screen.findByRole("button", { name: "Usar este objetivo" }));
    fireEvent.click(screen.getByRole("button", { name: "Reanalisar" }));
    await screen.findByText(/Nova análise — objetivo Dano \(de texto confirmado \(interpretador rules_v1\)\)/);
    expect(mock.callsTo("POST", /^\/api\/v1\/analyses$/)[0].body).toEqual({
      decisionId: DECISION,
      objective: { priority: "DAMAGE", parserVersion: "rules_v1" },
    });
    expect(screen.getByRole("heading", { name: "Comparação" })).toBeInTheDocument();
  });
  it("depois da comparação: reflexão uma vez, links de exportação e julgamento com abstenção", async () => {
    mock.on("POST", /^\/api\/v1\/learning-attempts$/, () => json(201, attempt()));
    mock.on("POST", /\/reveal$/, () => json(200, attempt({ status: "REVEALED", analysisId: COMPARISON.analysisId, comparison: COMPARISON, historicalItemId: 3031 })));
    mock.on("POST", /\/reflection$/, () =>
      json(200, attempt({ status: "REVEALED", analysisId: COMPARISON.analysisId, comparison: COMPARISON, historicalItemId: 3031, reflection: "<b>mais magos</b>" })),
    );
    mock.on("POST", /\/feedback$/, () =>
      json(201, { feedbackId: "44444444-4444-4444-8444-444444444444", analysisId: COMPARISON.analysisId, itemId: 3075, grade: null, reason: "ok", rubricVersion: "rubric_v1", reviewerRef: "abcdefabcdef" }),
    );
    render(<StudyPanel decisionId={DECISION} />);
    fireEvent.click(screen.getByRole("button", { name: /Confirmar objetivo/ }));
    await screen.findByText("Thornmail");
    fireEvent.click(screen.getByRole("button", { name: /Revelar/ }));
    await screen.findByRole("heading", { name: "Reflexão" });

    fireEvent.change(screen.getByLabelText(/O que mudaria na sua escolha/), { target: { value: "<b>mais magos</b>" } });
    fireEvent.click(screen.getByRole("button", { name: "Registrar reflexão" }));
    await screen.findByText(/Sua reflexão registrada/);
    expect(screen.getByText("<b>mais magos</b>")).toBeInTheDocument(); // texto, não HTML
    expect(mock.callsTo("POST", /\/reflection$/)[0].body).toEqual({ reflection: "<b>mais magos</b>" });

    const html = screen.getByRole("link", { name: /Baixar HTML/ });
    expect(html).toHaveAttribute("href", `/api/v1/analyses/${COMPARISON.analysisId}/report?format=html&attemptId=${ATTEMPT}`);
    expect(screen.getByRole("link", { name: /Baixar JSON/ })).toHaveAttribute("download");

    fireEvent.click(screen.getByText("Avaliação de revisor (opcional)"));
    fireEvent.change(screen.getByLabelText("Nota"), { target: { value: "abstain" } });
    fireEvent.change(screen.getByLabelText("Motivo"), { target: { value: "ok" } });
    fireEvent.change(screen.getByLabelText("Pseudônimo do revisor"), { target: { value: "rev-a" } });
    fireEvent.click(screen.getByRole("button", { name: "Registrar julgamento" }));
    await screen.findByText(/abstenção \(revisor abcdefabcdef\)/);
    expect(mock.callsTo("POST", /\/feedback$/)[0].body).toEqual({ itemId: 3075, grade: null, reason: "ok", rubricVersion: "rubric_v1", reviewerAlias: "rev-a" });
  });

  it("o foco acompanha cada passo quando o botão clicado some", async () => {
    mock.on("POST", /^\/api\/v1\/learning-attempts$/, () => json(201, attempt()));
    mock.on("POST", /\/hint$/, () =>
      json(200, attempt({ hintLevel: 1, hints: [{ level: 1, text: "O time inimigo tem dois atiradores.", source: "context.enemies" }] })),
    );
    mock.on("POST", /\/answer$/, () => json(200, attempt({ status: "ANSWERED", hintLevel: 1, hints: [{ level: 1, text: "O time inimigo tem dois atiradores.", source: "context.enemies" }], selectedItemId: 3075 })));
    mock.on("POST", /\/reveal$/, () =>
      json(200, attempt({ status: "REVEALED", selectedItemId: 3075, analysisId: COMPARISON.analysisId, comparison: COMPARISON, historicalItemId: 3031 })),
    );
    mock.on("POST", /\/reflection$/, () =>
      json(200, attempt({ status: "REVEALED", analysisId: COMPARISON.analysisId, comparison: COMPARISON, historicalItemId: 3031, reflection: "mais armadura" })),
    );
    render(<StudyPanel decisionId={DECISION} />);
    fireEvent.click(screen.getByRole("button", { name: /Confirmar objetivo/ }));
    await waitFor(() => expect(screen.getByText(/Qual destas você compraria/)).toHaveFocus());

    fireEvent.click(screen.getByRole("button", { name: /observar ameaças/ }));
    await waitFor(() => expect(screen.getByRole("list", { name: "Pistas abertas" })).toHaveFocus());

    fireEvent.click(screen.getByLabelText("Thornmail"));
    fireEvent.click(screen.getByRole("button", { name: "Responder" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Revelar/ })).toHaveFocus());

    fireEvent.click(screen.getByRole("button", { name: /Revelar/ }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Comparação" })).toHaveFocus());

    fireEvent.change(screen.getByLabelText(/O que mudaria na sua escolha/), { target: { value: "mais armadura" } });
    fireEvent.click(screen.getByRole("button", { name: "Registrar reflexão" }));
    await waitFor(() => expect(screen.getByText(/Sua reflexão registrada/)).toHaveFocus());
  });

  it("glossário curado está disponível antes e durante o estudo sem abrir pistas ou revelar respostas", async () => {
    mock.on("POST", /^\/api\/v1\/learning-attempts$/, () => json(201, attempt()));
    render(<StudyPanel decisionId={DECISION} />);
    expect(screen.getByText("Glossário")).toBeInTheDocument();
    expect(screen.getByText("Custo restante")).toBeInTheDocument();
    expect(screen.getByText(/Uma compra frequente não é necessariamente/)).toBeInTheDocument();
    expect(mock.callsTo("POST", /^\/api\/v1\/learning-attempts$/)).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: /Confirmar objetivo/ }));
    await screen.findByText("Thornmail");
    expect(screen.getByText("Glossário")).toBeInTheDocument();
    expect(mock.callsTo("POST", /\/hint$/)).toHaveLength(0);
    expect(mock.callsTo("POST", /\/reveal$/)).toHaveLength(0);
    expect(screen.queryByText(/Compra histórica/)).not.toBeInTheDocument();
  });

  it("modo sem pistas cria tentativa TRANSFER e não oferece pistas ou glossário", async () => {
    mock.on("POST", /^\/api\/v1\/learning-attempts$/, () => json(201, attempt({ mode: "TRANSFER" })));
    render(<StudyPanel decisionId={DECISION} />);
    fireEvent.click(screen.getByLabelText(/Praticar sem pistas/));
    expect(screen.queryByText("Glossário")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Confirmar objetivo/ }));
    await screen.findByText("Thornmail");
    expect(mock.callsTo("POST", /^\/api\/v1\/learning-attempts$/)[0].body).toMatchObject({ mode: "TRANSFER" });
    expect(screen.getByText(/Modo sem pistas \(transferência\)/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Pista/ })).toBeNull();
    expect(screen.queryByText("Glossário")).not.toBeInTheDocument();
  });
});

