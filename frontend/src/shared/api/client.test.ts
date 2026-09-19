import { describe, expect, it, vi } from "vitest";
import { createMockApi, decisionState, json, matchDetail, problem } from "../../test/mockApi";
import { createApiClient } from "./client";
import { ApiError } from "./errors";

const JOB_ID = "688bd44d-39b7-4f7b-956b-0fd4a63cfafd";

function clientWith(configure: (api: ReturnType<typeof createMockApi>) => void) {
  const api = createMockApi();
  configure(api);
  return { api, client: createApiClient(api.fetch as unknown as typeof fetch) };
}

describe("cliente da API", () => {
  it("createImport envia {matchId, mode} em POST /api/v1/imports", async () => {
    const { api, client } = clientWith((mock) =>
      mock.on("POST", /^\/api\/v1\/imports$/, () =>
        json(202, { jobId: JOB_ID, status: "QUEUED", statusUrl: `/api/v1/imports/${JOB_ID}` }),
      ),
    );

    const accepted = await client.createImport("DEMO-0008", "DEMO");

    expect(accepted.jobId).toBe(JOB_ID);
    expect(api.calls[0]).toMatchObject({
      method: "POST",
      path: "/api/v1/imports",
      body: { matchId: "DEMO-0008", mode: "DEMO" },
    });
  });

  it("422/RIOT_KEY_MISSING do servidor (modo RIOT sem chave) chega como ApiError tipado, sem tradução", async () => {
    const { client } = clientWith((mock) =>
      mock.on("POST", /^\/api\/v1\/imports$/, () => problem(422, "RIOT_KEY_MISSING")),
    );

    await expect(client.createImport("BR1_1", "RIOT")).rejects.toMatchObject({
      status: 422,
      code: "RIOT_KEY_MISSING",
      requestId: "req-123",
    });
  });

  it("422/VALIDATION_ERROR do servidor continua sendo VALIDATION_ERROR", async () => {
    const { client } = clientWith((mock) =>
      mock.on("POST", /^\/api\/v1\/imports$/, () => problem(422, "VALIDATION_ERROR")),
    );

    await expect(client.createImport("BR1_1", "RIOT")).rejects.toMatchObject({
      code: "VALIDATION_ERROR",
    });
  });

  it("HTTP_422 genérico não é mais reinterpretado como falta de chave (nenhum caso especial)", async () => {
    const { client } = clientWith((mock) =>
      mock.on("POST", /^\/api\/v1\/imports$/, () => problem(422, "HTTP_422")),
    );

    await expect(client.createImport("BR1_1", "RIOT")).rejects.toMatchObject({
      code: "HTTP_422",
    });
  });

  it("listMatches e listDecisions montam a query (cursor, limit, participantId)", async () => {
    const { api, client } = clientWith((mock) => {
      mock.on("GET", /^\/api\/v1\/matches$/, () => json(200, { items: [], nextCursor: null }));
      mock.on("GET", /^\/api\/v1\/matches\/DEMO-0008\/decisions$/, () =>
        json(200, { items: [], nextCursor: null }),
      );
    });

    await client.listMatches();
    await client.listMatches({ cursor: "abc_-", limit: 20 });
    await client.listDecisions("DEMO-0008", 3, { cursor: "xyz", limit: 5 });

    expect(api.calls[0].search.toString()).toBe("");
    expect(Object.fromEntries(api.calls[1].search)).toEqual({ cursor: "abc_-", limit: "20" });
    expect(Object.fromEntries(api.calls[2].search)).toEqual({
      participantId: "3",
      cursor: "xyz",
      limit: "5",
    });
  });

  it("getMatch, prepareMatch, getImport, retryImport e getDecision usam método e caminho do contrato", async () => {
    const { api, client } = clientWith((mock) => {
      mock.on("GET", /^\/api\/v1\/matches\/BR1_9$/, () => json(200, matchDetail()));
      mock.on("POST", /^\/api\/v1\/matches\/BR1_9\/preparation$/, () =>
        json(202, { status: "PENDING", errorCode: null, retryAt: null, attempts: 0 }),
      );
      mock.on("GET", /^\/api\/v1\/imports\/.+$/, () =>
        json(200, { status: "QUEUED", kind: "IMPORT", attempts: 0 }),
      );
      mock.on("POST", /^\/api\/v1\/imports\/.+\/retry$/, () =>
        json(202, { jobId: JOB_ID, status: "QUEUED", statusUrl: "/x" }),
      );
      mock.on("GET", /^\/api\/v1\/decisions\/.+$/, () => json(200, decisionState()));
    });

    await client.getMatch("BR1_9");
    await client.prepareMatch("BR1_9");
    await client.getImport(JOB_ID);
    await client.retryImport(JOB_ID);
    await client.getDecision("0f92d8d8-319c-4fa4-bca3-49157a1f9e00");

    expect(api.calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      "GET /api/v1/matches/BR1_9",
      "POST /api/v1/matches/BR1_9/preparation",
      `GET /api/v1/imports/${JOB_ID}`,
      `POST /api/v1/imports/${JOB_ID}/retry`,
      "GET /api/v1/decisions/0f92d8d8-319c-4fa4-bca3-49157a1f9e00",
    ]);
    expect(api.calls[1].body).toBeUndefined(); // POST sem corpo
  });

  it("resposta de erro vira ApiError tipado {status, code}", async () => {
    const { client } = clientWith((mock) =>
      mock.on("GET", /^\/api\/v1\/matches\/BR1_9$/, () => problem(404, "RESOURCE_NOT_FOUND")),
    );

    const error = await client.getMatch("BR1_9").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, code: "RESOURCE_NOT_FOUND" });
  });

  it("falha de rede vira NETWORK_ERROR (status 0)", async () => {
    const client = createApiClient(vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(client.getMatch("BR1_9")).rejects.toMatchObject({ status: 0, code: "NETWORK_ERROR" });
  });

  it("cancelamento propaga AbortError e não vira mensagem", async () => {
    const client = createApiClient(
      vi.fn().mockRejectedValue(new DOMException("cancelado", "AbortError")),
    );

    await expect(client.getMatch("BR1_9")).rejects.toMatchObject({ name: "AbortError" });
  });

  it("2xx com corpo que não é JSON vira INVALID_RESPONSE", async () => {
    const client = createApiClient(vi.fn().mockResolvedValue(new Response("não é json", { status: 200 })));

    await expect(client.getMatch("BR1_9")).rejects.toMatchObject({
      status: 200,
      code: "INVALID_RESPONSE",
    });
  });

  it("repassa o AbortSignal ao fetch", async () => {
    const { api, client } = clientWith((mock) =>
      mock.on("GET", /^\/api\/v1\/matches\/BR1_9$/, () => json(200, matchDetail())),
    );
    const controller = new AbortController();

    await client.getMatch("BR1_9", controller.signal);

    expect(api.calls[0].signal).toBe(controller.signal);
  });
});
