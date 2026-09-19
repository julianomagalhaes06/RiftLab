import type { Page, Request } from "@playwright/test";

/**
 * API simulada via `page.route` para os specs que precisam de controle total do
 * cenário (falhas recuperáveis, 360 px, teclado). O fluxo contra a stack REAL fica em
 * real-stack.spec.ts. Payloads no formato do OpenAPI gerado.
 */
export const JOB_ID = "688bd44d-39b7-4f7b-956b-0fd4a63cfafd";
export const JOB_ID_2 = "11111111-2222-4333-8444-555555555555";
export const DECISION_1 = "0f92d8d8-319c-4fa4-bca3-49157a1f9e00";
export const DECISION_2 = "0f92d8d8-319c-4fa4-bca3-49157a1f9e01";
export const LONG_MATCH_ID = "BR1_31203456789012345678";

export interface MockReply {
  status: number;
  body: unknown;
  contentType?: string;
}

export interface MockRequest {
  method: string;
  url: URL;
  body: unknown;
}

export type Handler = (request: MockRequest) => MockReply;

export function ok(body: unknown, status = 200): MockReply {
  return { status, body };
}

export function problem(status: number, code: string): MockReply {
  return {
    status,
    contentType: "application/problem+json",
    body: {
      type: `https://riftlab.local/problems/${code.toLowerCase()}`,
      title: "Titulo do servidor",
      status,
      code,
      detail: "detalhe-do-servidor-que-nao-pode-aparecer",
      requestId: "e2e-request-id",
    },
  };
}

export function job(overrides: Record<string, unknown> = {}) {
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

export function preparation(overrides: Record<string, unknown> = {}) {
  return { status: "READY", errorCode: null, retryAt: null, attempts: 1, ...overrides };
}

export function matchSummary(overrides: Record<string, unknown> = {}) {
  return {
    matchId: "DEMO-0008",
    queue: 420,
    map: 11,
    patch: "DEMO-1",
    startAt: "2026-09-01T12:00:00Z",
    preparationStatus: "READY",
    ...overrides,
  };
}

const TEN_PARTICIPANTS = Array.from({ length: 10 }, (_, index) => ({
  participantId: index + 1,
  championId: 9000 + index * 137,
  team: index < 5 ? 100 : 200,
  role: ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"][index % 5] as string | null,
}));

export function matchDetail(overrides: Record<string, unknown> = {}) {
  const prep = (overrides.preparation as Record<string, unknown> | undefined) ?? preparation();
  return {
    ...matchSummary({ preparationStatus: prep.status }),
    participants: TEN_PARTICIPANTS,
    preparation: prep,
    ...overrides,
  };
}

export function decisionSummary(overrides: Record<string, unknown> = {}) {
  return {
    decisionId: DECISION_1,
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
    decisionId: DECISION_1,
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

export type RouteKey =
  | "POST /api/v1/imports"
  | "GET /api/v1/imports/:id"
  | "POST /api/v1/imports/:id/retry"
  | "GET /api/v1/matches"
  | "GET /api/v1/matches/:id"
  | "POST /api/v1/matches/:id/preparation"
  | "GET /api/v1/matches/:id/decisions"
  | "GET /api/v1/decisions/:id";

const PATTERNS: Array<[RouteKey, string, RegExp]> = [
  ["POST /api/v1/imports", "POST", /^\/api\/v1\/imports$/],
  ["POST /api/v1/imports/:id/retry", "POST", /^\/api\/v1\/imports\/[^/]+\/retry$/],
  ["GET /api/v1/imports/:id", "GET", /^\/api\/v1\/imports\/[^/]+$/],
  ["GET /api/v1/matches", "GET", /^\/api\/v1\/matches$/],
  ["POST /api/v1/matches/:id/preparation", "POST", /^\/api\/v1\/matches\/[^/]+\/preparation$/],
  ["GET /api/v1/matches/:id/decisions", "GET", /^\/api\/v1\/matches\/[^/]+\/decisions$/],
  ["GET /api/v1/matches/:id", "GET", /^\/api\/v1\/matches\/[^/]+$/],
  ["GET /api/v1/decisions/:id", "GET", /^\/api\/v1\/decisions\/[^/]+$/],
];

/** Fluxo feliz padrão: demonstração importada e pronta, com decisões e detalhe. */
export const happyPath: Record<RouteKey, Handler> = {
  "POST /api/v1/imports": () =>
    ok({ jobId: JOB_ID, status: "QUEUED", statusUrl: `/api/v1/imports/${JOB_ID}` }, 202),
  "GET /api/v1/imports/:id": () =>
    ok(job({ status: "SUCCEEDED", attempts: 1, matchId: "DEMO-0008" })),
  "POST /api/v1/imports/:id/retry": () =>
    ok({ jobId: JOB_ID_2, status: "QUEUED", statusUrl: `/api/v1/imports/${JOB_ID_2}` }, 202),
  "GET /api/v1/matches": () => ok({ items: [matchSummary()], nextCursor: null }),
  "GET /api/v1/matches/:id": () => ok(matchDetail()),
  "POST /api/v1/matches/:id/preparation": () => ok(preparation({ status: "PENDING", attempts: 0 }), 202),
  "GET /api/v1/matches/:id/decisions": ({ url }) =>
    url.searchParams.get("cursor")
      ? ok({ items: [decisionSummary({ decisionId: DECISION_2, timestampMs: 900_000 })], nextCursor: null })
      : ok({
          items: [
            decisionSummary(),
            decisionSummary({
              decisionId: DECISION_2,
              timestampMs: 600_000,
              eligible: false,
              reasonCodes: ["INVENTORY_UNCERTAIN", "OUT_OF_SCOPE"],
              budgetSource: "UNKNOWN",
              frameAgeMs: null,
              mainItemOrdinal: 2,
            }),
          ],
          nextCursor: "PROXIMA_PAGINA",
        }),
  "GET /api/v1/decisions/:id": () => ok(decisionState()),
};

export interface CallLog {
  calls: string[];
  count(key: RouteKey): number;
}

/** Instala `/health/*` e `/api/v1/*` simulados; `overrides` substitui rotas específicas. */
export async function mockApi(
  page: Page,
  overrides: Partial<Record<RouteKey, Handler>> = {},
): Promise<CallLog> {
  const calls: string[] = [];
  await page.route("**/health/live", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"UP"}' }),
  );
  await page.route("**/health/ready", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: '{"status":"UP","database":"UP"}',
    }),
  );
  await page.route("**/api/v1/**", async (route) => {
    const request: Request = route.request();
    const url = new URL(request.url());
    const match = PATTERNS.find(([, method, pattern]) => request.method() === method && pattern.test(url.pathname));
    if (!match) {
      await route.fulfill(problem(500, "INTERNAL_ERROR") as never);
      return;
    }
    const key = match[0];
    calls.push(key);
    const handler = overrides[key] ?? happyPath[key];
    const reply = handler({
      method: request.method(),
      url,
      body: request.postData() ? JSON.parse(request.postData() as string) : undefined,
    });
    await route.fulfill({
      status: reply.status,
      contentType: reply.contentType ?? "application/json",
      body: JSON.stringify(reply.body),
    });
  });
  return { calls, count: (key) => calls.filter((call) => call === key).length };
}

/** Sequência de respostas: a última se repete. */
export function sequence(...replies: Array<() => MockReply>): Handler {
  let index = 0;
  return () => replies[Math.min(index++, replies.length - 1)]();
}
