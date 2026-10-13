import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMockApi, json, type MockApi } from "../../test/mockApi";
import { RouterProvider } from "../../shared/router/RouterProvider";
import { PurgePanel } from "./PurgePanel";

const MATCH = "DEMO-0008";
const JOB = "7d1f2a3b-0000-4000-8000-000000000001";
const PREVIEW = {
  matchId: MATCH,
  counts: { participants: 10, timelineEvents: 42, decisionStates: 6, decisionPoints: 3, analyses: 2, learningAttempts: 2, feedback: 1 },
  datasets: [{ task: "purchase_decision", patch: "16.19", description: "dataset de treino", alreadyInvalidated: false }],
  models: [{ name: "M1", kind: "MODEL", status: "ACTIVE" }],
  unverifiableDatasets: 1,
  limitations: ["Modelos já treinados não desaprendem."],
};

let mock: MockApi;
beforeEach(() => {
  mock = createMockApi();
  vi.stubGlobal("fetch", mock.fetch);
  mock.on("GET", /\/purge-preview$/, () => json(200, PREVIEW));
  mock.on("DELETE", /^\/api\/v1\/matches\/DEMO-0008$/, () => json(202, { jobId: JOB, status: "QUEUED", statusUrl: `/api/v1/imports/${JOB}` }));
});
afterEach(() => vi.unstubAllGlobals());

describe("PurgePanel (CC-603): remoção só com prévia e confirmação digitada", () => {
  it("mostra o que sai, exige o id exato e acompanha o job até concluir", async () => {
    mock.on("GET", /^\/api\/v1\/imports\//, () => json(200, { status: "SUCCEEDED", kind: "PURGE", attempts: 1, matchId: null }));
    render(
      <RouterProvider>
        <PurgePanel matchId={MATCH} />
      </RouterProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Ver o que será removido" }));
    expect(await screen.findByText("42 eventos da linha do tempo")).toBeInTheDocument();
    expect(screen.getByText(/1 dataset registrado lista esta partida e será invalidado/)).toBeInTheDocument();
    expect(screen.getByText(/1 versão de modelo treinada com ele sai de ativo/)).toBeInTheDocument();
    expect(screen.getByText("1 julgamento de revisor")).toBeInTheDocument();
    expect(screen.getByText("1 dataset sem lista de partidas não pôde ser verificado.")).toBeInTheDocument();
    expect(screen.getByText("Modelos já treinados não desaprendem.")).toBeInTheDocument();

    const confirm = screen.getByLabelText(/Digite o id da partida/);
    const remove = screen.getByRole("button", { name: "Remover definitivamente" });
    expect(remove).toBeDisabled();
    fireEvent.change(confirm, { target: { value: "DEMO-0001" } });
    expect(remove).toBeDisabled();
    expect(mock.callsTo("DELETE", /matches/)).toHaveLength(0);
    fireEvent.change(confirm, { target: { value: MATCH } });
    fireEvent.click(remove);

    const done = await screen.findByText(/Partida removida/);
    const [deleted] = mock.callsTo("DELETE", /matches/);
    expect(deleted.path).toBe(`/api/v1/matches/${MATCH}`);
    expect(deleted.search.get("confirm")).toBe(MATCH);
    await waitFor(() => expect(done.closest("[role=status]")).toHaveFocus());
    expect(screen.getByRole("link", { name: "Voltar ao Histórico" })).toBeInTheDocument();
  });

  it("falha do job aparece como alerta e permite tentar de novo", async () => {
    mock.on("GET", /^\/api\/v1\/imports\//, () => json(200, { status: "FAILED", kind: "PURGE", attempts: 5, errorCode: "PURGE_FAILED", matchId: null }));
    render(
      <RouterProvider>
        <PurgePanel matchId={MATCH} />
      </RouterProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Ver o que será removido" }));
    fireEvent.change(await screen.findByLabelText(/Digite o id da partida/), { target: { value: MATCH } });
    fireEvent.click(screen.getByRole("button", { name: "Remover definitivamente" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A remoção da partida falhou");
    expect(screen.getByRole("button", { name: "Remover definitivamente" })).toBeEnabled();
  });
});
