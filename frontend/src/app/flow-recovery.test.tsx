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
const JOB_ID_2 = "11111111-2222-4333-8444-555555555555";
const DECISION_ID = "0f92d8d8-319c-4fa4-bca3-49157a1f9e00";
const SERVER_DETAIL = "detalhe-do-servidor-que-nao-pode-aparecer";

function job(overrides: Record<string, unknown> = {}) {
  return { status: "QUEUED", kind: "IMPORT", attempts: 0, errorCode: null, retryAt: null, matchId: null, ...overrides };
}

function sequence(...responses: Array<() => Response>) {
  let index = 0;
  return () => responses[Math.min(index++, responses.length - 1)]();
}

let api: MockApi;

function setup(path: string, configure?: (mock: MockApi) => void) {
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

describe("importação: falha, nova tentativa e espera", () => {
  it("FAILED mostra texto útil sem segredo e 'Tentar importar novamente' refaz pelo servidor", async () => {
    setup(`/imports/${JOB_ID}`, (mock) => {
      mock.on("GET", new RegExp(`^/api/v1/imports/${JOB_ID}$`), () =>
        json(200, job({ status: "FAILED", attempts: 1, errorCode: "RIOT_AUTH_FAILED" })),
      );
      mock.on("GET", new RegExp(`^/api/v1/imports/${JOB_ID_2}$`), () =>
        json(200, job({ status: "SUCCEEDED", attempts: 1, matchId: "BR1_3120345678" })),
      );
      mock.on("POST", new RegExp(`^/api/v1/imports/${JOB_ID}/retry$`), () =>
        json(202, { jobId: JOB_ID_2, status: "QUEUED", statusUrl: `/api/v1/imports/${JOB_ID_2}` }),
      );
      mock.on("GET", /^\/api\/v1\/matches\/BR1_3120345678$/, () => json(200, matchDetail({ matchId: "BR1_3120345678" })));
    });

    expect(
      await screen.findByText(
        "A chave da Riot é inválida ou expirou; renove a chave Development no ambiente local.",
      ),
    ).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/RGAPI/);

    fireEvent.click(screen.getByRole("button", { name: "Tentar importar novamente" }));

    await screen.findByRole("heading", { name: /^Partida BR1_3120345678/ });
    expect(api.callsTo("POST", /retry$/)).toHaveLength(1);
    // depois do FAILED, nenhuma consulta extra ao job antigo
    expect(api.callsTo("GET", new RegExp(`imports/${JOB_ID}$`))).toHaveLength(1);
  });

  it.each([
    ["RIOT_NOT_FOUND", /A Riot não encontrou essa partida/],
    ["RIOT_RATE_LIMITED", /A Riot limitou temporariamente as requisições/],
    ["UPSTREAM_UNAVAILABLE", /A Riot está indisponível/],
    ["SCHEMA_UNSUPPORTED", /formato dos dados dessa partida não é compatível/],
    ["RETRY_LIMIT_EXCEEDED", /falhou várias vezes seguidas/],
    ["CODIGO_NUNCA_VISTO", /Não foi possível concluir a operação/],
  ])("errorCode %s vira o texto da tabela única", async (code, expected) => {
    setup(`/imports/${JOB_ID}`, (mock) =>
      mock.on("GET", /^\/api\/v1\/imports\/.+$/, () =>
        json(200, job({ status: "FAILED", errorCode: code })),
      ),
    );

    expect(await screen.findByText(expected)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/andamento/i);
  });

  it("RETRY_WAIT mostra nova tentativa agendada e só volta a consultar depois do retryAt", async () => {
    const retryAt = new Date(Date.now() + 30_000).toISOString();
    setup(`/imports/${JOB_ID}`, (mock) =>
      mock.on(
        "GET",
        /^\/api\/v1\/imports\/.+$/,
        sequence(
          () => json(200, job({ status: "RETRY_WAIT", attempts: 1, errorCode: "UPSTREAM_UNAVAILABLE", retryAt })),
          () => json(200, job({ status: "RUNNING", attempts: 2 })),
        ),
      ),
    );

    await waitFor(() => expect(status()).toMatch(/Nova tentativa agendada/));
    expect(screen.getByText(/A Riot está indisponível/)).toBeInTheDocument();
    expect(screen.getByText(/Próxima tentativa por volta de/)).toBeInTheDocument();

    await advance(29_000);
    expect(api.callsTo("GET", /imports\//)).toHaveLength(1); // respeita o retryAt
    await advance(2_000);
    await waitFor(() => expect(status()).toMatch(/Importando a partida/));
  });

  it("sair da tela cancela a consulta em voo e para o polling", async () => {
    setup(`/imports/${JOB_ID}`, (mock) =>
      mock.on("GET", /^\/api\/v1\/imports\/.+$/, () => json(200, job({ status: "RUNNING", attempts: 1 }))),
    );
    await waitFor(() => expect(status()).toMatch(/Importando a partida/));
    const polls = api.callsTo("GET", /imports\//).length;

    fireEvent.click(screen.getByRole("link", { name: "Voltar ao início" }));
    await screen.findByRole("heading", { name: /Entenda cada compra/ });
    await advance(120_000);

    expect(api.callsTo("GET", /imports\//).length).toBe(polls);
  });

  it("job inexistente (404) mostra texto sem afirmar 'em andamento' e sem botão de repetir", async () => {
    setup(`/imports/${JOB_ID}`, (mock) =>
      mock.on("GET", /^\/api\/v1\/imports\/.+$/, () => problem(404, "RESOURCE_NOT_FOUND", SERVER_DETAIL)),
    );

    expect(await screen.findByText(/Não encontramos o que você procurou/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(SERVER_DETAIL);
    expect(document.body).not.toHaveTextContent(/andamento/);
    expect(screen.queryByRole("button", { name: "Consultar novamente" })).not.toBeInTheDocument();
  });
});

describe("partida: preparação com falha e nova tentativa", () => {
  it("FAILED mostra o motivo e 'Tentar preparar novamente' acompanha até READY", async () => {
    setup("/matches/DEMO-0008", (mock) => {
      mock.on(
        "GET",
        /^\/api\/v1\/matches\/DEMO-0008$/,
        sequence(
          () => json(200, matchDetail({ preparation: preparation({ status: "FAILED", errorCode: "TIMELINE_EVENT_MALFORMED" }) })),
          () => json(200, matchDetail({ preparation: preparation({ status: "PENDING", attempts: 0 }) })),
          () => json(200, matchDetail()),
        ),
      );
      mock.on("POST", /^\/api\/v1\/matches\/DEMO-0008\/preparation$/, () =>
        json(202, preparation({ status: "PENDING", attempts: 0 })),
      );
    });

    expect(await screen.findByText(/Um evento da linha do tempo está malformado/)).toBeInTheDocument();
    expect(status()).toMatch(/A preparação da partida falhou/);
    // FAILED é terminal: o polling parou
    await advance(60_000);
    expect(api.callsTo("GET", /matches\/DEMO-0008$/)).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Tentar preparar novamente" }));

    await waitFor(() => expect(status()).toMatch(/Preparando a partida: aguardando na fila/));
    await advance(2_000);
    await waitFor(() => expect(status()).toMatch(/Partida pronta/));
    expect(api.callsTo("POST", /preparation$/)).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Tentar preparar novamente" })).not.toBeInTheDocument();
  });

  it("NOT_REQUESTED mostra o placar e oferece 'Revisar partida' (revisão sob demanda)", async () => {
    setup("/matches/DEMO-0008", (mock) => {
      mock.on(
        "GET",
        /^\/api\/v1\/matches\/DEMO-0008$/,
        sequence(
          () => json(200, matchDetail({ preparation: preparation({ status: "NOT_REQUESTED", attempts: 0 }) })),
          () => json(200, matchDetail()),
        ),
      );
      mock.on("POST", /^\/api\/v1\/matches\/DEMO-0008\/preparation$/, () =>
        json(202, preparation({ status: "PENDING", attempts: 0 })),
      );
    });

    expect(await screen.findByRole("heading", { name: "Revisão da partida" })).toBeInTheDocument();
    expect(screen.getByText(/Revise a partida para ver o ouro/)).toBeInTheDocument();
    // O placar vem da importação: já aparece antes de qualquer revisão.
    expect(screen.getByRole("heading", { name: "Placar" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Revisar partida" }));

    await waitFor(() => expect(status()).toMatch(/Partida pronta/));
  });

  it("erro ao pedir a preparação (404) aparece sem afirmar 'em andamento'", async () => {
    setup("/matches/DEMO-0008", (mock) => {
      mock.on("GET", /^\/api\/v1\/matches\/DEMO-0008$/, () =>
        json(200, matchDetail({ preparation: preparation({ status: "FAILED", errorCode: "RAW_MATCH_NOT_CACHED" }) })),
      );
      mock.on("POST", /^\/api\/v1\/matches\/DEMO-0008\/preparation$/, () => problem(404, "RESOURCE_NOT_FOUND"));
    });
    fireEvent.click(await screen.findByRole("button", { name: "Tentar preparar novamente" }));

    expect(await screen.findAllByText(/Não encontramos o que você procurou/)).not.toHaveLength(0);
    expect(document.body).not.toHaveTextContent(/andamento/);
  });

  it("PENDING aguardando nova tentativa mostra o motivo da falha anterior e o horário", async () => {
    const retryAt = new Date(Date.now() + 40_000).toISOString();
    setup("/matches/DEMO-0008", (mock) =>
      mock.on("GET", /^\/api\/v1\/matches\/DEMO-0008$/, () =>
        json(200, matchDetail({ preparation: preparation({ status: "PENDING", errorCode: "UPSTREAM_UNAVAILABLE", retryAt, attempts: 1 }) })),
      ),
    );

    expect(await screen.findByText(/A tentativa anterior falhou: A Riot está indisponível/)).toBeInTheDocument();
    expect(screen.getByText(/Nova tentativa por volta de/)).toBeInTheDocument();
  });

  it("partida inexistente (404): mensagem, sem repetição automática", async () => {
    setup("/matches/BR1_999", (mock) =>
      mock.on("GET", /^\/api\/v1\/matches\/BR1_999$/, () => problem(404, "RESOURCE_NOT_FOUND", SERVER_DETAIL)),
    );

    expect(await screen.findByText(/Não encontramos o que você procurou/)).toBeInTheDocument();
    await advance(60_000);
    expect(api.callsTo("GET", /matches\/BR1_999$/)).toHaveLength(1);
    expect(document.body).not.toHaveTextContent(SERVER_DETAIL);
  });

  it("participante inexistente na URL avisa sem quebrar", async () => {
    setup("/matches/DEMO-0008?participant=9", (mock) =>
      mock.on("GET", /^\/api\/v1\/matches\/DEMO-0008$/, () => json(200, matchDetail())),
    );

    expect(await screen.findByText("O participante 9 não existe nesta partida.")).toBeInTheDocument();
    expect(api.callsTo("GET", /decisions$/)).toHaveLength(0);
  });
});

describe("decisões: erros e estados", () => {
  const readyMatch = (mock: MockApi) =>
    mock.on("GET", /^\/api\/v1\/matches\/DEMO-0008$/, () => json(200, matchDetail()));

  it("DECISIONS_NOT_PREPARED (409) tem texto próprio e 'Recarregar decisões'", async () => {
    setup("/matches/DEMO-0008?participant=1", (mock) => {
      readyMatch(mock);
      mock.on(
        "GET",
        /decisions$/,
        sequence(
          () => problem(409, "DECISIONS_NOT_PREPARED"),
          () => json(200, { items: [], nextCursor: null }),
        ),
      );
    });

    // As decisões ficam na aba Compras da análise.
    fireEvent.click(await screen.findByRole("tab", { name: "Compras" }));
    expect(await screen.findByText(/As decisões dessa partida ainda não foram preparadas/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Recarregar decisões" }));

    // 200 com items=[] é diferente de "não preparado"
    expect(
      await screen.findByText("Nenhuma decisão de compra foi encontrada para este participante nesta partida."),
    ).toBeInTheDocument();
  });

  it("INVALID_CURSOR ao carregar mais avisa e recarrega do início", async () => {
    setup("/matches/DEMO-0008?participant=1", (mock) => {
      readyMatch(mock);
      mock.on("GET", /decisions$/, (call) =>
        call.search.get("cursor")
          ? problem(422, "INVALID_CURSOR")
          : json(200, { items: [decisionSummary()], nextCursor: "VELHO" }),
      );
    });
    fireEvent.click(await screen.findByRole("tab", { name: "Compras" }));
    fireEvent.click(await screen.findByRole("button", { name: "Carregar mais decisões" }));

    expect(await screen.findByText(/A página solicitada não é mais válida/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Decisão em 7:52" })).toBeInTheDocument(); // o que já estava na tela fica
    fireEvent.click(screen.getByRole("button", { name: "Recarregar decisões" }));
    await waitFor(() => expect(api.callsTo("GET", /decisions$/).length).toBeGreaterThanOrEqual(3));
  });

  it("decisão inelegível por motivo do alvo mostra só o texto neutro", async () => {
    setup(`/decisions/${DECISION_ID}`, (mock) =>
      mock.on("GET", /^\/api\/v1\/decisions\/.+$/, () =>
        json(200, decisionState({ eligible: false, reasons: [{ code: "TARGET_NOT_ELIGIBLE", subReasons: [] }] })),
      ),
    );

    expect(await screen.findByText("Esta decisão não é elegível para análise.")).toBeInTheDocument();
    expect(screen.getByText("Inelegível para análise")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/item comprado|desfe|revert|alvo|classe do/i);
  });

  it("orçamento com quadro antigo: 'snapshot antigo; orçamento não confirmável', com a idade", async () => {
    const stale = {
      value: 640,
      source: "FRAME_ESTIMATE",
      frameAgeMs: 472_000,
      staleFrame: true,
      codes: ["STALE_FRAME"],
      immediatePurchaseAssertable: false,
    };
    setup(`/decisions/${DECISION_ID}`, (mock) =>
      mock.on("GET", /^\/api\/v1\/decisions\/.+$/, () => json(200, decisionState({ budget: stale }))),
    );

    expect(await screen.findByText("Snapshot antigo; orçamento não confirmável")).toBeInTheDocument();
    expect(screen.getByText(/há 7 min 52 s/)).toBeInTheDocument();
    expect(screen.getByText("Não é possível afirmar que a compra imediata era possível.")).toBeInTheDocument();
  });

  it("orçamento desconhecido é 'Orçamento desconhecido' e nunca 0", async () => {
    setup(`/decisions/${DECISION_ID}`, (mock) =>
      mock.on("GET", /^\/api\/v1\/decisions\/.+$/, () =>
        json(200, decisionState({
          budget: { value: null, source: "UNKNOWN", frameAgeMs: null, staleFrame: false, codes: ["UNKNOWN_BUDGET"], immediatePurchaseAssertable: false },
          level: { value: null, frameAgeMs: null, absentReason: "NO_PRIOR_FRAME" },
        })),
      ),
    );

    expect(await screen.findByText("Orçamento desconhecido")).toBeInTheDocument();
    expect(screen.getByText("Nível não observado: não há quadro anterior à decisão")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/\b0 de ouro/);
  });

  it("inventário incerto avisa; versões ficam no bloco recolhível 'detalhes técnicos'", async () => {
    setup(`/decisions/${DECISION_ID}`, (mock) =>
      mock.on("GET", /^\/api\/v1\/decisions\/.+$/, () =>
        json(200, decisionState({
          eligible: false,
          reasons: [{ code: "INVENTORY_UNCERTAIN", subReasons: ["ITEM_UNKNOWN"] }],
        })),
      ),
    );

    expect(await screen.findByText(/Inventário incerto: os itens abaixo são o melhor estado reconstruído/)).toBeInTheDocument();
    const summary = screen.getByText("Detalhes técnicos");
    const details = summary.closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(false); // recolhido por padrão
    expect(details).toHaveTextContent("decision_selection_v1");
    expect(details).toHaveTextContent("INVENTORY_UNCERTAIN: item desconhecido no catálogo");
    expect(details).toHaveTextContent("11111111-1111-4111-8111-111111111111");
  });

  it("decisão inexistente (404) mostra a mensagem e o caminho de volta", async () => {
    setup(`/decisions/${DECISION_ID}`, (mock) =>
      mock.on("GET", /^\/api\/v1\/decisions\/.+$/, () => problem(404, "RESOURCE_NOT_FOUND")),
    );

    expect(await screen.findByText(/Não encontramos o que você procurou/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Voltar ao início" })).toHaveAttribute("href", "/");
  });
});

describe("URL: deep link, recarregar e voltar/avançar", () => {
  it("abre direto uma partida com participante escolhido (retomada por URL)", async () => {
    // A aba também vem do endereço (?aba=compras), como ao voltar da página da decisão.
    setup("/matches/DEMO-0008?participant=1&aba=compras", (mock) => {
      mock.on("GET", /^\/api\/v1\/matches\/DEMO-0008$/, () => json(200, matchDetail()));
      mock.on("GET", /decisions$/, () => json(200, { items: [decisionSummary()], nextCursor: null }));
    });

    expect(await screen.findByRole("link", { name: "Decisão em 7:52" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Compras" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("link", { name: /Participante 1/, hidden: true })).toHaveAttribute("aria-current", "true");
    expect(api.callsTo("POST", /./)).toHaveLength(0); // retomar nunca dispara escrita
  });

  it("endereço desconhecido mostra 'Página não encontrada'", async () => {
    setup("/qualquer/coisa");

    expect(await screen.findByRole("heading", { name: "Página não encontrada" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Voltar ao início" }));
    expect(await screen.findByRole("heading", { name: /Entenda cada compra/ })).toBeInTheDocument();
  });

  it("voltar e avançar do navegador percorrem as telas", async () => {
    setup("/historico", (mock) => {
      mock.on("GET", /^\/api\/v1\/matches$/, () => json(200, { items: [{ ...matchDetail(), participants: undefined, preparation: undefined }], nextCursor: null }));
      mock.on("GET", /^\/api\/v1\/matches\/DEMO-0008$/, () => json(200, matchDetail()));
      mock.on("GET", /decisions$/, () => json(200, { items: [], nextCursor: null }));
    });
    // O Histórico abre na conta configurada; a partida de demonstração fica no filtro DEMO.
    fireEvent.click(await screen.findByRole("button", { name: "DEMO" }));
    fireEvent.click(await screen.findByRole("link", { name: "Contexto da partida DEMO-0008" }));
    await screen.findByRole("heading", { name: /^Partida DEMO-0008/ });
    fireEvent.click(await screen.findByRole("link", { name: /Participante 2/ }));
    // Escolhido no placar, o jogador fica destacado e o Placar continua aberto (o foco não some);
    // as decisões dele ficam na aba Compras.
    expect(await screen.findByRole("tab", { name: "Placar" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("tab", { name: "Compras" }));
    await screen.findByRole("heading", { name: "Decisões de Campeão #9002" });

    window.history.back();
    await waitFor(() => expect(window.location.search).toBe(""));
    await screen.findByText("Selecione um participante no placar para ver a revisão, as compras e as decisões dele.");

    window.history.back();
    await screen.findByRole("heading", { name: "Partidas para estudar" });

    window.history.forward();
    await screen.findByRole("heading", { name: /^Partida DEMO-0008/ });
  });

  it("o protótipo do CC-102 fica fora da jornada principal e rotulado como ilustrativo", async () => {
    setup("/importar");

    const button = await screen.findByRole("button", { name: "Abrir protótipo (dados ilustrativos)" });
    expect(screen.getByText(/dados fictícios e sem relação com a API/)).toBeInTheDocument();

    fireEvent.click(button);
    expect(await screen.findByText(/dados ilustrativos, sem importação real/)).toBeInTheDocument();
    expect(window.location.pathname).toBe("/prototipo");
    expect(document.title).toMatch(/Protótipo \(dados ilustrativos\)/);
  });
});
