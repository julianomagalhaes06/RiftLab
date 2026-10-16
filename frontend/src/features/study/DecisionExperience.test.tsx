import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RouterProvider } from "../../shared/router/RouterProvider";
import { createMockApi, json, type MockApi } from "../../test/mockApi";
import { DecisionExperience } from "./DecisionExperience";

const decisionId = "22222222-2222-4222-8222-222222222222";
const result = {
  analysisId: "33333333-3333-4333-8333-333333333333", decisionId, status: "AVAILABLE", engine: "RULES",
  objective: { priority: "BALANCED" }, versions: { catalog: "16.19/16.19.1", features: "f", candidates: "c" },
  support: { level: "LIMITED", reasonCodes: [] }, quality: {},
  candidates: [{ itemId: 3031, name: "Gume do Infinito", rank: 1, remainingCost: 2000,
    explanations: [{ text: "Faltam 2000 de ouro.", source: "candidates.remainingCost" }] }],
};
let mock: MockApi;
beforeEach(() => {
  window.history.replaceState(null, "", `/decisions/${decisionId}`);
  mock = createMockApi();
  vi.stubGlobal("fetch", mock.fetch);
});
afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });
function open() { render(<RouterProvider><DecisionExperience decisionId={decisionId} /></RouterProvider>); }

describe("Analisar como padrão, praticar opcionalmente", () => {
  it("compara diretamente com objetivo equilibrado sem criar tentativa ou exigir resposta", async () => {
    mock.on("POST", /^\/api\/v1\/analyses$/, () => json(201, result));
    open();
    await screen.findByText("1. Gume do Infinito");
    expect(screen.getByText("Faltam 2000 de ouro.")).toBeInTheDocument();
    expect(screen.queryByText(/Qual destas você compraria/)).not.toBeInTheDocument();
    expect(mock.callsTo("POST", /learning-attempts/)).toHaveLength(0);
    expect(mock.callsTo("POST", /^\/api\/v1\/analyses$/)[0].body).toEqual({ decisionId, objective: { priority: "BALANCED" } });
    expect(screen.getByRole("link", { name: "Baixar análise HTML" })).not.toHaveAttribute("href", expect.stringContaining("attemptId"));
  });

  it("o link explícito de prática não analisa nem revela automaticamente", () => {
    window.history.replaceState(null, "", `/decisions/${decisionId}?mode=practice`);
    open();
    expect(screen.getByRole("heading", { name: "Estudar esta decisão" })).toBeInTheDocument();
    expect(mock.callsTo("POST", /analyses|learning-attempts/)).toHaveLength(0);
    expect(screen.queryByRole("heading", { name: "Comparação" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Praticar decisão" })).toHaveAttribute("aria-pressed", "true");
  });

  it("preserva abstenção sem inventar uma recomendação", async () => {
    mock.on("POST", /^\/api\/v1\/analyses$/, () => json(201, { ...result, status: "UNSUPPORTED", candidates: [], support: { level: "NONE", reasonCodes: ["PATCH_UNSUPPORTED"] } }));
    open();
    await screen.findByText("Não há comparação disponível para esta decisão.");
    expect(screen.getByText("Não há regras de itens para o patch desta partida.")).toBeInTheDocument();
    expect(screen.queryByText("1. Gume do Infinito")).not.toBeInTheDocument();
  });

  it("permite repetir uma análise que falhou", async () => {
    let failed = true;
    mock.on("POST", /^\/api\/v1\/analyses$/, () => failed ? json(503, {}) : json(201, result));
    open();
    const retry = await screen.findByRole("button", { name: "Tentar análise novamente" });
    failed = false;
    fireEvent.click(retry);
    await screen.findByText("1. Gume do Infinito");
    expect(mock.callsTo("POST", /^\/api\/v1\/analyses$/)).toHaveLength(2);
  });

  it("cancelar a análise ao entrar na prática impede resultado tardio nessa experiência", async () => {
    let resolve!: (response: Response) => void;
    mock.on("POST", /^\/api\/v1\/analyses$/, () => new Promise<Response>((done) => { resolve = done; }));
    open();
    fireEvent.click(screen.getByRole("button", { name: "Praticar decisão" }));
    expect(mock.callsTo("POST", /^\/api\/v1\/analyses$/)[0].signal?.aborted).toBe(true);
    await act(async () => { resolve(json(201, result)); });
    expect(screen.queryByRole("heading", { name: "Comparação" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Estudar esta decisão" })).toBeInTheDocument();
  });
});
