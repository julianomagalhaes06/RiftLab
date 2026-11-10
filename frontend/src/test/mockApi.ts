import { vi } from "vitest";

export interface RecordedCall {
  method: string;
  path: string;
  search: URLSearchParams;
  body: unknown;
  signal: AbortSignal | undefined;
}

type Responder = (call: RecordedCall) => Response | Promise<Response>;

interface Route {
  method: string;
  pattern: RegExp;
  respond: Responder;
}

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Resposta de erro no formato problem+json do backend. */
export function problem(status: number, code: string, detail = "detalhe do servidor"): Response {
  return new Response(
    JSON.stringify({
      type: `https://riftlab.local/problems/${code.toLowerCase()}`,
      title: "Titulo do servidor",
      status,
      code,
      detail,
      requestId: "req-123",
    }),
    { status, headers: { "content-type": "application/problem+json" } },
  );
}

/**
 * `fetch` falso roteado por método + caminho (regex). Sem rota casada, responde 500
 * (o teste esqueceu de mockar algo). Health responde 200 por padrão.
 */
export function createMockApi() {
  const routes: Route[] = [
    { method: "GET", pattern: /^\/health\/live$/, respond: () => json(200, { status: "UP" }) },
    { method: "GET", pattern: /^\/api\/v1\/auth\/session$/, respond: () => json(200, { authRequired: false, user: null, demoEnabled: true }) },
    {
      method: "GET",
      pattern: /^\/health\/ready$/,
      respond: () => json(200, { status: "UP", database: "UP" }),
    },
  ];
  const calls: RecordedCall[] = [];

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const call: RecordedCall = {
      method,
      path: url.pathname,
      search: url.searchParams,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      signal: init?.signal ?? undefined,
    };
    calls.push(call);
    const route = [...routes]
      .reverse()
      .find((candidate) => candidate.method === method && candidate.pattern.test(url.pathname));
    if (!route) {
      return json(500, { unmocked: `${method} ${url.pathname}` });
    }
    return route.respond(call);
  });

  return {
    fetch: fetchMock,
    calls,
    on(method: string, pattern: RegExp, respond: Responder) {
      routes.push({ method, pattern, respond });
    },
    callsTo(method: string, pattern: RegExp): RecordedCall[] {
      return calls.filter((call) => call.method === method && pattern.test(call.path));
    },
  };
}

export type MockApi = ReturnType<typeof createMockApi>;

// --- fábricas de payloads (formato do OpenAPI gerado) ---

export function matchSummary(overrides: Record<string, unknown> = {}) {
  return {
    matchId: "DEMO-0008",
    queue: 420,
    map: 11,
    patch: "DEMO-1",
    startAt: "2026-09-01T12:00:00Z",
    durationSeconds: 1800,
    preparationStatus: "READY",
    supportLevel: typeof overrides.matchId === "string" && overrides.matchId.startsWith("BR1_") ? "LIMITED" : "DEMO",
    supportReason: typeof overrides.matchId === "string" && overrides.matchId.startsWith("BR1_") ? "PATCH_NOT_VALIDATED" : "DEMO_DATA",
    championsPresent: [
      { championId: 9001, name: null },
      { championId: 9002, name: null },
    ],
    ...overrides,
  };
}

export function preparation(overrides: Record<string, unknown> = {}) {
  return { status: "READY", errorCode: null, retryAt: null, attempts: 1, ...overrides };
}

export function matchDetail(overrides: Record<string, unknown> = {}) {
  const prep = (overrides.preparation as Record<string, unknown> | undefined) ?? preparation();
  return {
    ...matchSummary({ preparationStatus: prep.status }),
    participants: [
      { participantId: 1, championId: 9001, team: 100, role: "BOTTOM" },
      { participantId: 2, championId: 9002, team: 200, role: null },
    ],
    preparation: prep,
    ...overrides,
  };
}

export function decisionSummary(overrides: Record<string, unknown> = {}) {
  return {
    decisionId: "0f92d8d8-319c-4fa4-bca3-49157a1f9e00",
    participantId: 1,
    eventSourceIndex: 3,
    timestampMs: 472_000,
    eligible: true,
    reasonCodes: [],
    mainItemOrdinal: 1,
    budgetSource: "FRAME_ESTIMATE",
    frameAgeMs: 20_000,
    staleFrame: false,
    ...overrides,
  };
}

export function decisionState(overrides: Record<string, unknown> = {}) {
  return {
    decisionId: "0f92d8d8-319c-4fa4-bca3-49157a1f9e00",
    matchId: "DEMO-0008",
    participantId: 1,
    eventSourceIndex: 3,
    timestampMs: 472_000,
    eligible: true,
    mainItemOrdinal: 1,
    reasons: [],
    inventory: {
      items: [
        { itemId: 100001, quantity: 2, name: "Componente Sintético A" },
        { itemId: 100002, quantity: 1, name: null },
      ],
      trinketItemId: null,
    },
    budget: {
      value: 640,
      source: "FRAME_ESTIMATE",
      frameAgeMs: 20_000,
      staleFrame: false,
      codes: [],
      immediatePurchaseAssertable: true,
    },
    level: { value: 4, frameAgeMs: 20_000, absentReason: null },
    versions: {
      reconstructionVersion: 6,
      selectionVersion: "decision_selection_v1",
      featureSchemaVersion: "feature_schema_v1",
      scopeVersion: "demo_scope_v1",
      catalogId: "11111111-1111-4111-8111-111111111111",
      curationRulesVersion: "data_dragon_v5",
      classificationVersion: "item_classification_v1",
    },
    ...overrides,
  };
}
