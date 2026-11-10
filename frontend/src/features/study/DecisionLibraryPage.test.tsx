import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../app/App";
import { createMockApi, json } from "../../test/mockApi";
import type { LearningAttemptSummary } from "../../shared/api/client";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

function attempt(overrides: Partial<LearningAttemptSummary> = {}): LearningAttemptSummary {
  return {
    attemptId: "11111111-1111-1111-1111-111111111111",
    decisionId: "22222222-2222-2222-2222-222222222222",
    matchId: "DEMO-0008",
    participantId: 1,
    status: "OPEN",
    mode: "STUDY",
    skipped: false,
    hasReflection: false,
    analysisId: null,
    createdAt: "2026-11-05T10:00:00Z",
    revealedAt: null,
    ...overrides,
  };
}

describe("Biblioteca de decisões", () => {
  it("lista as tentativas, mostra relatório só quando revelada e carrega mais", async () => {
    window.history.replaceState(null, "", "/estudo/biblioteca");
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/learning-attempts$/, ({ search }) =>
      json(
        200,
        search.has("cursor")
          ? { items: [attempt({ attemptId: "attempt-2", status: "ANSWERED" })], nextCursor: null }
          : {
              items: [
                attempt({
                  attemptId: "attempt-1",
                  status: "REVEALED",
                  hasReflection: true,
                  analysisId: "33333333-3333-3333-3333-333333333333",
                }),
              ],
              nextCursor: "next",
            },
      ));
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);

    expect(await screen.findByText("Biblioteca de decisões")).toBeInTheDocument();
    expect(await screen.findByText("Partida DEMO-0008")).toBeInTheDocument();
    expect(screen.getByText("Revelada")).toBeInTheDocument();
    expect(screen.getByText(/com reflexão/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ver relatório" })).toHaveAttribute(
      "href",
      "/api/v1/analyses/33333333-3333-3333-3333-333333333333/report?format=html&attemptId=attempt-1",
    );
    expect(screen.getByRole("link", { name: "Abrir decisão" })).toHaveAttribute(
      "href",
      "/decisions/22222222-2222-2222-2222-222222222222",
    );

    fireEvent.click(screen.getByRole("button", { name: "Carregar mais tentativas" }));
    expect(await screen.findByText("Respondida")).toBeInTheDocument();
    // só a tentativa revelada tem relatório; a recém-carregada (ANSWERED) não.
    expect(screen.getAllByRole("link", { name: "Ver relatório" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Carregar mais tentativas" })).not.toBeInTheDocument();
  });

  it("estado vazio explica o que preencher a biblioteca", async () => {
    window.history.replaceState(null, "", "/estudo/biblioteca");
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/learning-attempts$/, () => json(200, { items: [], nextCursor: null }));
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);
    expect(await screen.findByText("Nenhuma tentativa ainda")).toBeInTheDocument();
  });
});
