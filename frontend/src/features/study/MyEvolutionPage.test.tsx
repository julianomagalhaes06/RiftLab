import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../app/App";
import { createMockApi, json } from "../../test/mockApi";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const PROGRESS = {
  study: { attempts: 5, answered: 5, skipped: 0, revealed: 4, usedHints: 1, reflections: 2 },
  transfer: { attempts: 0, answered: 0, skipped: 0, revealed: 0, usedHints: 0, reflections: 0 },
};

describe("Minha evolução", () => {
  it("mostra os totais e as tentativas agrupadas por semana", async () => {
    window.history.replaceState(null, "", "/estudo/evolucao");
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/learning-attempts\/progress$/, () => json(200, PROGRESS));
    api.on("GET", /^\/api\/v1\/learning-attempts$/, () =>
      json(200, {
        items: [
          {
            attemptId: "a1",
            decisionId: "d1",
            matchId: "DEMO-0008",
            participantId: 1,
            status: "REVEALED",
            mode: "STUDY",
            skipped: false,
            hasReflection: true,
            analysisId: "an1",
            createdAt: "2026-11-02T10:00:00Z",
            revealedAt: "2026-11-02T10:05:00Z",
          },
          {
            attemptId: "a2",
            decisionId: "d2",
            matchId: "DEMO-0008",
            participantId: 1,
            status: "OPEN",
            mode: "STUDY",
            skipped: false,
            hasReflection: false,
            analysisId: null,
            createdAt: "2026-11-03T10:00:00Z",
            revealedAt: null,
          },
        ],
        nextCursor: null,
      }));
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);

    expect(await screen.findByText("Minha evolução")).toBeInTheDocument();
    expect(await screen.findByText("5")).toBeInTheDocument(); // tentativas no total
    expect(screen.getByText("2")).toBeInTheDocument(); // com reflexão
    expect(await screen.findByText("02–08 nov")).toBeInTheDocument();
    expect(screen.getByText(/2 tentativas/)).toBeInTheDocument();
    expect(screen.getByText(/1 reflexão/)).toBeInTheDocument();
  });

  it("estado vazio explica o que vai aparecer", async () => {
    window.history.replaceState(null, "", "/estudo/evolucao");
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/learning-attempts\/progress$/, () =>
      json(200, {
        study: { attempts: 0, answered: 0, skipped: 0, revealed: 0, usedHints: 0, reflections: 0 },
        transfer: { attempts: 0, answered: 0, skipped: 0, revealed: 0, usedHints: 0, reflections: 0 },
      }));
    api.on("GET", /^\/api\/v1\/learning-attempts$/, () => json(200, { items: [], nextCursor: null }));
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);
    expect(await screen.findByText("Nada ainda nesta janela")).toBeInTheDocument();
  });
});
