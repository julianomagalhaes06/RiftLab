import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createMockApi,
  decisionState,
  decisionSummary,
  json,
  matchDetail,
  preparation,
  problem,
  type MockApi,
} from "../test/mockApi";
import App from "./App";

const JOB_ID = "688bd44d-39b7-4f7b-956b-0fd4a63cfafd";
const DECISION_1 = "0f92d8d8-319c-4fa4-bca3-49157a1f9e00";
const DECISION_2 = "0f92d8d8-319c-4fa4-bca3-49157a1f9e01";
const DECISION_3 = "0f92d8d8-319c-4fa4-bca3-49157a1f9e02";

function job(overrides: Record<string, unknown> = {}) {
  return {
    status: "QUEUED",
    kind: "IMPORT",
    attempts: 0,
    errorCode: null,
    retryAt: null,
    matchId: null,
    ...overrides,
  };
}

/** Responde em ordem; a última resposta se repete (estado estável). */
function sequence(...responses: Array<() => Response>) {
  let index = 0;
  return () => responses[Math.min(index++, responses.length - 1)]();
}

let api: MockApi;

function setup(path = "/", configure?: (mock: MockApi) => void) {
  api = createMockApi();
  api.on("GET", /^\/api\/v1\/matches$/, () => json(200, { items: [], nextCursor: null }));
  configure?.(api);
  vi.stubGlobal("fetch", api.fetch);
  window.history.replaceState(null, "", path);
  return render(<App />);
}

const advance = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));
const status = () => screen.getAllByRole("status").map((el) => el.textContent ?? "").join(" | ");

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("fluxo principal: demonstração → importação → partida → decisão", () => {
  it("percorre a jornada inteira só pela interface, sem console", async () => {
    setup("/importar", (mock) => {
      mock.on("POST", /^\/api\/v1\/imports$/, () =>
        json(202, { jobId: JOB_ID, status: "QUEUED", statusUrl: `/api/v1/imports/${JOB_ID}` }),
      );
      mock.on(
        "GET",
        /^\/api\/v1\/imports\/.+$/,
        sequence(
          () => json(200, job({ status: "QUEUED" })),
          () => json(200, job({ status: "RUNNING", attempts: 1 })),
          () => json(200, job({ status: "SUCCEEDED", attempts: 1, matchId: "DEMO-0008" })),
        ),
      );
      mock.on(
        "GET",
        /^\/api\/v1\/matches\/DEMO-0008$/,
        sequence(
          () => json(200, matchDetail({ preparation: preparation({ status: "PENDING", attempts: 0 }) })),
          () => json(200, matchDetail({ preparation: preparation({ status: "RUNNING", attempts: 1 }) })),
          () => json(200, matchDetail()),
        ),
      );
      mock.on("GET", /^\/api\/v1\/matches\/DEMO-0008\/decisions$/, (call) =>
        call.search.get("cursor")
          ? json(200, {
              items: [decisionSummary({ decisionId: DECISION_3, timestampMs: 900_000 })],
              nextCursor: null,
            })
          : json(200, {
              items: [
                decisionSummary({ decisionId: DECISION_1 }),
                decisionSummary({
                  decisionId: DECISION_2,
                  timestampMs: 600_000,
                  eligible: false,
                  reasonCodes: ["INVENTORY_UNCERTAIN"],
                  budgetSource: "UNKNOWN",
                  frameAgeMs: null,
                  mainItemOrdinal: 2,
                }),
              ],
              nextCursor: "PROXIMA_PAGINA",
            }),
      );
      mock.on("GET", /^\/api\/v1\/decisions\/.+$/, () => json(200, decisionState()));
    });

    // Entrada: aviso pós-partida, sem botão de acompanhar ao vivo.
    expect(
      await screen.findByText("Analise partidas concluídas. O RiftLab não acompanha jogos em andamento."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /ao vivo|acompanhar/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Explorar demonstração" }));

    // Progresso da importação com aria-live.
    await screen.findByRole("heading", { name: "Importação da partida" });
    expect(api.callsTo("POST", /\/api\/v1\/imports$/)[0].body).toEqual({
      matchId: "DEMO-0008",
      mode: "DEMO",
    });
    expect(window.location.pathname).toBe(`/imports/${JOB_ID}`);
    await waitFor(() => expect(status()).toMatch(/Na fila para importar/));
    await advance(1_000);
    await waitFor(() => expect(status()).toMatch(/Importando a partida/));
    await advance(2_000);

    // SUCCEEDED leva à página da partida (replace: sem voltar para o progresso).
    await screen.findByRole("heading", { name: /^Partida DEMO-0008/ });
    expect(window.location.pathname).toBe("/matches/DEMO-0008");
    await waitFor(() => expect(status()).toMatch(/aguardando na fila/i));
    await advance(1_000);
    await waitFor(() => expect(status()).toMatch(/reconstruindo o inventário/i));
    await advance(2_000);
    await waitFor(() => expect(status()).toMatch(/Partida pronta/));

    // Participantes → seleção → decisões.
    expect(screen.getByText("Time azul")).toBeInTheDocument();
    expect(screen.getByText("Atirador")).toBeInTheDocument();
    expect(screen.getByText("Função não informada")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: /Participante 1 — Campeão #9001/ }));
    await screen.findByRole("heading", { name: "Decisões de Campeão #9001 (Atirador)" });
    expect(window.location.search).toBe("?participant=1");
    expect(api.callsTo("GET", /decisions$/)[0].search.get("participantId")).toBe("1");

    expect(await screen.findByRole("link", { name: "Decisão em 7:52" })).toBeInTheDocument();
    expect(screen.getByText("Analisar")).toBeInTheDocument();
    expect(screen.getAllByText("Bloqueada").length).toBeGreaterThan(0);
    expect(screen.getByText(/O inventário anterior à decisão é incerto/)).toBeInTheDocument();
    expect(screen.getByText("2º item principal")).toBeInTheDocument();

    // "Carregar mais" usa o cursor devolvido pela API.
    fireEvent.click(screen.getByRole("button", { name: "Carregar mais decisões" }));
    expect(await screen.findByRole("link", { name: "Decisão em 15:00" })).toBeInTheDocument();
    expect(api.callsTo("GET", /decisions$/)[1].search.get("cursor")).toBe("PROXIMA_PAGINA");
    expect(screen.queryByRole("button", { name: "Carregar mais decisões" })).not.toBeInTheDocument();

    // Detalhe da decisão.
    fireEvent.click(screen.getByRole("link", { name: "Decisão em 7:52" }));
    await screen.findByRole("heading", { name: /em 7:52/ });
    expect(window.location.pathname).toBe(`/decisions/${DECISION_1}`);
    expect(await screen.findByText("Componente Sintético A")).toBeInTheDocument();
    expect(screen.getByText("×2")).toBeInTheDocument();
    expect(screen.getByText("Item #100002, nome indisponível")).toBeInTheDocument();
    expect(screen.getByText("Amuleto: Vazio")).toBeInTheDocument();
    expect(screen.getByText("Nível 4 (quadro há 20 s)")).toBeInTheDocument();
    expect(screen.getByText(/Aproximadamente 640 de ouro/)).toBeInTheDocument();

    // Volta às decisões do mesmo participante.
    fireEvent.click(screen.getByRole("link", { name: /^Voltar às decisões da partida$/ }));
    await screen.findByRole("heading", { name: "Decisões de Campeão #9001 (Atirador)" });
    expect(window.location.search).toBe("?participant=1");

    // Nada consulta o progresso da importação depois do SUCCEEDED, nem a partida depois de READY.
    const importPolls = api.callsTo("GET", /\/api\/v1\/imports\//).length;
    const matchPolls = api.callsTo("GET", /\/api\/v1\/matches\/DEMO-0008$/).length;
    await advance(120_000);
    expect(api.callsTo("GET", /\/api\/v1\/imports\//).length).toBe(importPolls);
    expect(api.callsTo("GET", /\/api\/v1\/matches\/DEMO-0008$/).length).toBe(matchPolls);
    expect(importPolls).toBe(3);
  });
});

describe("importar partida: formulário de partida real", () => {
  it("valida o padrão BR1 no cliente antes de qualquer requisição", async () => {
    setup("/importar");
    const input = await screen.findByLabelText(/ID da partida/);

    fireEvent.change(input, { target: { value: "DEMO-0001" } });
    fireEvent.click(screen.getByRole("button", { name: "Analisar partida" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/formato BR1_ seguido de números/);
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(api.callsTo("POST", /imports/)).toHaveLength(0);
  });

  it("envia o ID válido no modo RIOT e vai para o progresso", async () => {
    setup("/importar", (mock) =>
      mock.on("POST", /^\/api\/v1\/imports$/, () =>
        json(202, { jobId: JOB_ID, status: "QUEUED", statusUrl: `/api/v1/imports/${JOB_ID}` }),
      ),
    );
    fireEvent.change(await screen.findByLabelText(/ID da partida/), {
      target: { value: " BR1_3120345678 " },
    });

    fireEvent.click(screen.getByRole("button", { name: "Analisar partida" }));

    await screen.findByRole("heading", { name: "Importação da partida" });
    expect(api.callsTo("POST", /imports$/)[0].body).toEqual({
      matchId: "BR1_3120345678",
      mode: "RIOT",
    });
  });

  it("422 do servidor no modo RIOT sem chave mostra o texto útil, sem corpo remoto", async () => {
    setup("/importar", (mock) =>
      mock.on("POST", /^\/api\/v1\/imports$/, () => problem(422, "RIOT_KEY_MISSING", "RIOT mode indisponível sem chave")),
    );
    fireEvent.change(await screen.findByLabelText(/ID da partida/), {
      target: { value: "BR1_1" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Analisar partida" }));

    const alert = await screen.findByText(/este ambiente não tem chave da Riot configurada/);
    expect(alert).toBeInTheDocument();
    expect(screen.getByText(/Explore a demonstração/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("RIOT mode indisponível sem chave");
    expect(screen.getByRole("button", { name: "Analisar partida" })).toBeEnabled();
  });

  it("VALIDATION_ERROR do servidor também é tratado", async () => {
    setup("/importar", (mock) =>
      mock.on("POST", /^\/api\/v1\/imports$/, () => problem(422, "VALIDATION_ERROR")),
    );
    fireEvent.change(await screen.findByLabelText(/ID da partida/), { target: { value: "BR1_1" } });

    fireEvent.click(screen.getByRole("button", { name: "Analisar partida" }));

    expect(await screen.findByText(/Os dados enviados não são válidos/)).toBeInTheDocument();
    expect(screen.getByText(/ID da requisição: req-123/)).toBeInTheDocument();
  });

  it("API fora do ar ao enviar mostra a mensagem de rede", async () => {
    setup("/importar", (mock) =>
      mock.on("POST", /^\/api\/v1\/imports$/, () => {
        throw new TypeError("Failed to fetch");
      }),
    );

    fireEvent.click(await screen.findByRole("button", { name: "Explorar demonstração" }));

    expect(await screen.findByText(/Não foi possível falar com a API do RiftLab/)).toBeInTheDocument();
  });
});

describe("decisão reconstruída com regras herdadas", () => {
  it("avisa de onde vieram as regras e mostra a origem nos detalhes técnicos", async () => {
    setup(`/decisions/${DECISION_1}`, (mock) => {
      mock.on("GET", /^\/api\/v1\/decisions\/.+$/, () =>
        json(
          200,
          decisionState({
            versions: {
              ...decisionState().versions,
              reconstructionVersion: 10,
              reconstructionRules: "INHERITED",
              reconstructionRulesPatch: "16.19",
            },
          }),
        ),
      );
      mock.on("GET", /^\/api\/v1\/matches\/DEMO-0008$/, () => json(200, matchDetail({ patch: "16.20" })));
      mock.on("GET", /^\/api\/v1\/matches\/DEMO-0008\/decisions$/, () => json(200, { items: [], nextCursor: null }));
    });

    const note = await screen.findByText(/regras confirmadas no patch 16\.19, aplicadas por herança/);
    await waitFor(() => expect(note).toHaveTextContent("ao patch 16.20"));
    fireEvent.click(screen.getByText("Detalhes técnicos"));
    expect(screen.getByText("herdadas do patch 16.19 (sem confirmação própria nesta fila)")).toBeInTheDocument();
  });

  it("decisão Flex com regras herdadas avisa que foram medidas em Solo/Duo", async () => {
    setup(`/decisions/${DECISION_1}`, (mock) => {
      mock.on("GET", /^\/api\/v1\/decisions\/.+$/, () =>
        json(
          200,
          decisionState({
            versions: {
              ...decisionState().versions,
              reconstructionRules: "INHERITED",
              reconstructionRulesPatch: "16.19",
              reconstructionRulesHealth: "UNVERIFIED",
            },
          }),
        ),
      );
      mock.on("GET", /^\/api\/v1\/matches\/DEMO-0008$/, () => json(200, matchDetail({ patch: "16.20", queue: 440 })));
      mock.on("GET", /^\/api\/v1\/matches\/DEMO-0008\/decisions$/, () => json(200, { items: [], nextCursor: null }));
    });
    const note = await screen.findByText(/regras confirmadas no patch 16\.19, aplicadas por herança/);
    await waitFor(() => expect(note).toHaveTextContent("medidas em Solo/Duo; o Flex ainda não tem amostra suficiente"));
  });

  it("confirmação própria de uma fila aparece com a fila nos detalhes técnicos", async () => {
    setup(`/decisions/${DECISION_1}`, (mock) => {
      mock.on("GET", /^\/api\/v1\/decisions\/.+$/, () =>
        json(
          200,
          decisionState({
            versions: {
              ...decisionState().versions,
              reconstructionRules: "VALIDATED",
              reconstructionRulesPatch: "16.20",
              reconstructionRulesQueue: 420,
            },
          }),
        ),
      );
    });
    await screen.findByRole("heading", { name: /^Decisão em 7:52/ });
    expect(screen.queryByText(/aplicadas por herança/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Detalhes técnicos"));
    expect(screen.getByText("confirmadas neste patch na fila Solo/Duo")).toBeInTheDocument();
  });

  it("não mostra o aviso quando as regras são do próprio patch", async () => {
    setup(`/decisions/${DECISION_1}`, (mock) => {
      mock.on("GET", /^\/api\/v1\/decisions\/.+$/, () =>
        json(200, decisionState({ versions: { ...decisionState().versions, reconstructionRules: "VALIDATED" } })),
      );
    });
    await screen.findByRole("heading", { name: /^Decisão em 7:52/ });
    expect(screen.queryByText(/aplicadas por herança/)).not.toBeInTheDocument();
  });
});
