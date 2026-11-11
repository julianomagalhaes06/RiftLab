import type { Page, Request } from "@playwright/test";
import { dashboard } from "../../../src/test/dashboard";
import { championPatterns } from "../../../src/test/championPatterns";

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
  /** 0 = nunca responde (simula interpretador travado; o cliente decide o timeout). */
  status: number;
  body: unknown;
  contentType?: string;
  headers?: Record<string, string>;
}

export const HANG: MockReply = { status: 0, body: null };

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
    durationSeconds: 1800,
    championsPresent: [{ championId: 9000, name: null }],
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
      reconstructionRules: "NONE",
      reconstructionRulesHealth: "UNVERIFIED",
      reconstructionRulesCleanParticipants: 0,
      reconstructionRulesFailures: 0,
    },
    ...overrides,
  };
}

/** Estado da sincronização do histórico (CC-901). */
export const PLAYER_ID = "7a5c3f1e-0000-4000-8000-000000000001";

/** Perfil buscado por Riot ID (CC-1101): ícone, nível e elo sintéticos. */
export function playerProfile(overrides: Record<string, unknown> = {}) {
  return {
    id: PLAYER_ID,
    gameName: "Jogador Teste",
    tagLine: "BR1",
    profileIconId: 4567,
    summonerLevel: 321,
    refreshedAt: "2026-10-19T15:00:00Z",
    dataDragonVersion: "16.20.1",
    solo: { queue: "RANKED_SOLO_5x5", state: "READY", tier: "GOLD", division: "II", leaguePoints: 41, wins: 10, losses: 8, observedAt: "2026-10-19T15:00:00Z" },
    flex: { queue: "RANKED_FLEX_SR", state: "UNRANKED", observedAt: "2026-10-19T15:00:00Z" },
    staleReason: null,
    ...overrides,
  };
}

export function historySync(overrides: Record<string, unknown> = {}) {
  return {
    state: "IDLE",
    reason: null,
    requestedAt: null,
    finishedAt: null,
    nextAllowedAt: null,
    listed: 0,
    newEnqueued: 0,
    alreadyKnown: 0,
    skippedPurged: 0,
    ...overrides,
  };
}

/** Revisão da partida (F1): uma compra comum, sem habilidades, rota nem gráfico. */
export function matchReview(overrides: Record<string, unknown> = {}) {
  return {
    matchId: "DEMO-0008",
    participantId: 1,
    championId: 9001,
    role: "BOTTOM",
    patch: "DEMO-1",
    queue: 420,
    referenceVersion: "16.19-solo-v1",
    referenceInherited: true,
    purchases: [
      { decisionId: DECISION_1, timestampMs: 472_000, ordinal: 1, itemId: 3031, status: "READY", label: "COMMON", share: 0.4, rank: 1, support: 50, referenceTop: [{ itemId: 3031, share: 0.4 }] },
    ],
    skills: null,
    lane: null,
    winProbability: null,
    winProbabilityUnavailable: "FRAMES_WITHOUT_XP",
    counts: { purchases: 1, ready: 1, common: 1, alternative: 0, uncommon: 0, noReference: 0 },
    ...overrides,
  };
}

export type RouteKey =
  | "GET /api/v1/champions/patterns"
  | "POST /api/v1/imports"
  | "GET /api/v1/imports/:id"
  | "POST /api/v1/imports/:id/retry"
  | "GET /api/v1/matches"
  | "GET /api/v1/matches/:id"
  | "GET /api/v1/matches/:id/timeline"
  | "GET /api/v1/matches/:id/review"
  | "POST /api/v1/matches/:id/preparation"
  | "GET /api/v1/matches/:id/decisions"
  | "GET /api/v1/decisions/:id"
  | "GET /api/v1/dashboard"
  | "GET /api/v1/ranked/me"
  | "GET /api/v1/auth/session"
  | "POST /api/v1/auth/login"
  | "POST /api/v1/auth/register"
  | "POST /api/v1/auth/verify-email"
  | "POST /api/v1/auth/resend-code"
  | "POST /api/v1/auth/password/forgot"
  | "POST /api/v1/auth/password/reset"
  | "POST /api/v1/auth/logout"
  | "POST /api/v1/history/sync"
  | "GET /api/v1/history/sync"
  | "POST /api/v1/players/lookup"
  | "POST /api/v1/players/lookup-from-match"
  | "GET /api/v1/players/me"
  | "POST /api/v1/players/live"
  | "POST /api/v1/players/live/status"
  | "GET /api/v1/catalog/current"
  | "POST /api/v1/players/:id/sync"
  | "GET /api/v1/players/:id/sync"
  | "GET /api/v1/matches/:id/purge-preview"
  | "DELETE /api/v1/matches/:id"
  | "POST /api/v1/objectives/interpret"
  | "GET /api/v1/learning-attempts/progress"
  | "GET /api/v1/learning-attempts/gamification"
  | "POST /api/v1/learning-attempts"
  | "POST /api/v1/learning-attempts/:id/hint"
  | "POST /api/v1/learning-attempts/:id/answer"
  | "POST /api/v1/learning-attempts/:id/reveal"
  | "POST /api/v1/learning-attempts/:id/reflection"
  | "POST /api/v1/analyses"
  | "POST /api/v1/analyses/:id/feedback"
  | "GET /api/v1/analyses/:id/report";

const PATTERNS: Array<[RouteKey, string, RegExp]> = [
  ["GET /api/v1/champions/patterns", "GET", /^\/api\/v1\/champions\/patterns$/],
  ["POST /api/v1/imports", "POST", /^\/api\/v1\/imports$/],
  ["POST /api/v1/imports/:id/retry", "POST", /^\/api\/v1\/imports\/[^/]+\/retry$/],
  ["GET /api/v1/imports/:id", "GET", /^\/api\/v1\/imports\/[^/]+$/],
  ["GET /api/v1/matches", "GET", /^\/api\/v1\/matches$/],
  ["GET /api/v1/matches/:id/timeline", "GET", /^\/api\/v1\/matches\/[^/]+\/timeline$/],
  ["GET /api/v1/matches/:id/review", "GET", /^\/api\/v1\/matches\/[^/]+\/review$/],
  ["POST /api/v1/matches/:id/preparation", "POST", /^\/api\/v1\/matches\/[^/]+\/preparation$/],
  ["GET /api/v1/matches/:id/decisions", "GET", /^\/api\/v1\/matches\/[^/]+\/decisions$/],
  ["GET /api/v1/matches/:id/purge-preview", "GET", /^\/api\/v1\/matches\/[^/]+\/purge-preview$/],
  ["DELETE /api/v1/matches/:id", "DELETE", /^\/api\/v1\/matches\/[^/]+$/],
  ["GET /api/v1/matches/:id", "GET", /^\/api\/v1\/matches\/[^/]+$/],
  ["GET /api/v1/decisions/:id", "GET", /^\/api\/v1\/decisions\/[^/]+$/],
  ["GET /api/v1/dashboard", "GET", /^\/api\/v1\/dashboard$/],
  ["GET /api/v1/ranked/me", "GET", /^\/api\/v1\/ranked\/me$/],
  ["GET /api/v1/auth/session", "GET", /^\/api\/v1\/auth\/session$/],
  ["POST /api/v1/auth/login", "POST", /^\/api\/v1\/auth\/login$/],
  ["POST /api/v1/auth/register", "POST", /^\/api\/v1\/auth\/register$/],
  ["POST /api/v1/auth/verify-email", "POST", /^\/api\/v1\/auth\/verify-email$/],
  ["POST /api/v1/auth/resend-code", "POST", /^\/api\/v1\/auth\/resend-code$/],
  ["POST /api/v1/auth/password/forgot", "POST", /^\/api\/v1\/auth\/password\/forgot$/],
  ["POST /api/v1/auth/password/reset", "POST", /^\/api\/v1\/auth\/password\/reset$/],
  ["POST /api/v1/auth/logout", "POST", /^\/api\/v1\/auth\/logout$/],
  ["POST /api/v1/history/sync", "POST", /^\/api\/v1\/history\/sync$/],
  ["GET /api/v1/history/sync", "GET", /^\/api\/v1\/history\/sync$/],
  ["POST /api/v1/players/lookup", "POST", /^\/api\/v1\/players\/lookup$/],
  ["POST /api/v1/players/lookup-from-match", "POST", /^\/api\/v1\/players\/lookup-from-match$/],
  ["GET /api/v1/players/me", "GET", /^\/api\/v1\/players\/me$/],
  ["POST /api/v1/players/live", "POST", /^\/api\/v1\/players\/live$/],
  ["POST /api/v1/players/live/status", "POST", /^\/api\/v1\/players\/live\/status$/],
  ["GET /api/v1/catalog/current", "GET", /^\/api\/v1\/catalog\/current$/],
  ["POST /api/v1/players/:id/sync", "POST", /^\/api\/v1\/players\/[^/]+\/sync$/],
  ["GET /api/v1/players/:id/sync", "GET", /^\/api\/v1\/players\/[^/]+\/sync$/],
  ["POST /api/v1/objectives/interpret", "POST", /^\/api\/v1\/objectives\/interpret$/],
  ["GET /api/v1/learning-attempts/progress", "GET", /^\/api\/v1\/learning-attempts\/progress$/],
  ["GET /api/v1/learning-attempts/gamification", "GET", /^\/api\/v1\/learning-attempts\/gamification$/],
  ["POST /api/v1/learning-attempts", "POST", /^\/api\/v1\/learning-attempts$/],
  ["POST /api/v1/learning-attempts/:id/hint", "POST", /^\/api\/v1\/learning-attempts\/[^/]+\/hint$/],
  ["POST /api/v1/learning-attempts/:id/answer", "POST", /^\/api\/v1\/learning-attempts\/[^/]+\/answer$/],
  ["POST /api/v1/learning-attempts/:id/reveal", "POST", /^\/api\/v1\/learning-attempts\/[^/]+\/reveal$/],
  ["POST /api/v1/learning-attempts/:id/reflection", "POST", /^\/api\/v1\/learning-attempts\/[^/]+\/reflection$/],
  ["POST /api/v1/analyses", "POST", /^\/api\/v1\/analyses$/],
  ["POST /api/v1/analyses/:id/feedback", "POST", /^\/api\/v1\/analyses\/[^/]+\/feedback$/],
  ["GET /api/v1/analyses/:id/report", "GET", /^\/api\/v1\/analyses\/[^/]+\/report$/],
];

export const ATTEMPT_ID = "a77e0000-0000-4000-8000-000000000001";
export const ANALYSIS_ID = "a7a10000-0000-4000-8000-000000000001";
export const STUDY_OPTIONS = [
  { itemId: 200001, name: "Item Composto Sintético" },
  { itemId: 200002, name: "Outro Composto Sintético" },
  { itemId: 200003, name: "Terceiro Composto Sintético" },
];

/** Comparação revelada (candidatos do gerador legal, critério identificado, sem gabarito). */
export function comparison(objective: Record<string, unknown>) {
  return {
    analysisId: ANALYSIS_ID,
    decisionId: DECISION_1,
    status: "AVAILABLE",
    engine: "RULES",
    objective,
    versions: { model: null, catalog: "DEMO-1/1", features: "feature_schema_v1", candidates: "legal_v1", ranking: "rules_v1", objective: "objective_v1", itemRules: "demo.json" },
    quality: { budgetSource: "FRAME_ESTIMATE", frameAgeMs: 20000, warnings: [] },
    support: { level: "SUPPORTED", reasonCodes: [], legalCandidates: 3, referenceDecisions: 0 },
    candidates: STUDY_OPTIONS.map((option, index) => ({
      ...option,
      rank: index + 1,
      baseRank: index === 0 ? 2 : index === 1 ? 1 : 3,
      score: 1 - index / 10,
      remainingCost: 150 + index * 400,
      affordability: "UNKNOWN",
      evidenceCodes: [],
      tags: ["Armor"],
      explanations: [{ text: `Faltam ${150 + index * 400} de ouro para completar o item.`, source: "candidates.remainingCost" }],
    })),
    scoreNote: "Pontuações são relativas a esta decisão; não são probabilidades.",
  };
}

/**
 * Tentativa de estudo com estado por spec (CC-304/305/505): objetivo e modo vêm do POST,
 * pistas, resposta, revelação e reflexão avançam a mesma tentativa.
 */
function studyHandlers(): Partial<Record<RouteKey, Handler>> {
  let state: Record<string, unknown> = {};
  const reply = (patch: Record<string, unknown>, status = 200) => {
    state = { ...state, ...patch };
    return ok(state, status);
  };
  return {
    "GET /api/v1/learning-attempts/gamification": ({ url }) => ok(gamification(url.searchParams.get("matchId"))),
    "GET /api/v1/learning-attempts/progress": () =>
      ok({ study: { attempts: 0, reflections: 0, usedHints: 0 }, transfer: { attempts: 0, reflections: 0 } }),
    "POST /api/v1/learning-attempts": ({ body }) => {
      const request = body as { objective: Record<string, unknown>; mode?: string; decisionId: string };
      state = {
        attemptId: ATTEMPT_ID,
        decisionId: request.decisionId,
        status: "OPEN",
        mode: request.mode ?? "STUDY",
        objective: request.objective,
        context: {},
        options: STUDY_OPTIONS,
        hintLevel: 0,
        hints: [],
        reasonCodes: [],
        selectedItemId: null,
        skipped: false,
        rationale: null,
        analysisId: null,
        comparison: null,
        historicalItemId: null,
        reflection: null,
      };
      return ok(state, 201);
    },
    "POST /api/v1/learning-attempts/:id/hint": ({ body }) => {
      const level = (body as { level: number }).level;
      const hints = [{ level: 1, text: "O time inimigo tem dois personagens de dano físico.", source: "context.enemies" }];
      if (level >= 2) {
        hints.push({ level: 2, text: "Item Composto Sintético: faltam 150 de ouro; atributos: Armor.", source: "candidates.remainingCost" });
      }
      return reply({ hintLevel: level, hints });
    },
    "POST /api/v1/learning-attempts/:id/answer": ({ body }) => {
      const answer = body as { itemId: number | null; skipped: boolean; rationale: string | null };
      return reply({ status: "ANSWERED", selectedItemId: answer.itemId, skipped: answer.skipped, rationale: answer.rationale });
    },
    "POST /api/v1/learning-attempts/:id/reveal": () =>
      reply({
        status: "REVEALED",
        analysisId: ANALYSIS_ID,
        comparison: comparison(state.objective as Record<string, unknown>),
        historicalItemId: 200002,
      }),
    "POST /api/v1/learning-attempts/:id/reflection": ({ body }) =>
      reply({ reflection: (body as { reflection: string }).reflection }),
    "POST /api/v1/analyses": ({ body }) =>
      ok({ ...comparison((body as { objective: Record<string, unknown> }).objective), analysisId: "a7a10000-0000-4000-8000-000000000002" }, 201),
    "POST /api/v1/analyses/:id/feedback": ({ body }) => {
      const feedback = body as { itemId: number; grade: number | null; reason: string; rubricVersion: string };
      return ok({ feedbackId: "fb000000-0000-4000-8000-000000000001", analysisId: ANALYSIS_ID, ...feedback, reviewerRef: "0123456789ab" }, 201);
    },
    "GET /api/v1/analyses/:id/report": ({ url }) =>
      url.searchParams.get("format") === "html"
        ? {
            status: 200,
            contentType: "text/html; charset=utf-8",
            headers: { "content-disposition": 'attachment; filename="riftlab-relatorio.html"' },
            body: "<!doctype html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\"><title>Relatório RiftLab</title></head><body><h1>Relatório de análise de compra</h1><p>Resposta inicial: Item Composto Sintético</p></body></html>",
          }
        : {
            status: 200,
            contentType: "application/json",
            headers: { "content-disposition": 'attachment; filename="riftlab-relatorio.json"' },
            body: { reportVersion: "report_v1", analysisId: ANALYSIS_ID },
          },
    "POST /api/v1/objectives/interpret": ({ body }) => {
      const text = String((body as { text: string }).text).toLowerCase();
      const priority = text.includes("aguentar") ? "SURVIVAL" : text.includes("dano") ? "DAMAGE" : null;
      return priority
        ? ok({ status: "INTERPRETED", objective: { priority, parserVersion: "rules_v1" }, parserVersion: "rules_v1", provider: "RULES", reasonCodes: [] })
        : ok({ status: "NEEDS_INPUT", objective: null, parserVersion: "rules_v1", provider: "RULES", reasonCodes: ["AMBIGUOUS_OR_OUT_OF_SCOPE"] });
    },
  };
}

/** Fluxo feliz padrão: demonstração importada e pronta, com decisões e detalhe. */
export const happyPath: Partial<Record<RouteKey, Handler>> = {
  "GET /api/v1/champions/patterns": () => ok(championPatterns({ champions: [], status: "EMPTY", patterns: [], situations: [] })),
  "POST /api/v1/imports": () =>
    ok({ jobId: JOB_ID, status: "QUEUED", statusUrl: `/api/v1/imports/${JOB_ID}` }, 202),
  "GET /api/v1/imports/:id": () =>
    ok(job({ status: "SUCCEEDED", attempts: 1, matchId: "DEMO-0008" })),
  "POST /api/v1/imports/:id/retry": () =>
    ok({ jobId: JOB_ID_2, status: "QUEUED", statusUrl: `/api/v1/imports/${JOB_ID_2}` }, 202),
  "GET /api/v1/matches": () => ok({ items: [matchSummary()], nextCursor: null }),
  "GET /api/v1/matches/:id": () => ok(matchDetail()),
  "GET /api/v1/matches/:id/timeline": () => ok({
    matchId: "DEMO-0008",
    dataDragonVersion: null,
    participants: [{ participantId: 1, items: [{ timestampMs: 472_000, itemId: 3031, kind: "PURCHASE", itemClass: "MAIN" }], skills: [] }],
    kills: [],
    objectives: [],
    goldFrames: [],
  }),
  "GET /api/v1/matches/:id/review": ({ url }) => ok(matchReview({ participantId: Number(url.searchParams.get("participantId") ?? 1) })),
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
  "GET /api/v1/dashboard": () => ok(dashboard({ status: "IDENTITY_UNAVAILABLE", totalMatches: 0, sampledMatches: 0, winSamples: 0, wins: 0, winRate: null, kdaSamples: 0, kda: null, distinctChampions: 0, champions: [], recentMatches: [], progress: { attempts: 0, revealed: 0, reflections: 0, matches: 0 } })),
  "GET /api/v1/ranked/me": () => ok({ status: "UNAVAILABLE", reason: "NO_OBSERVATION", position: null, change: null }),
  // Sprint 2 (#10): sem login exigido por padrão, como numa instalação de uma pessoa.
  "GET /api/v1/auth/session": () => ok({ authRequired: false, user: null, demoEnabled: true }),
  "POST /api/v1/auth/login": () => problem(401, "INVALID_CREDENTIALS"),
  "POST /api/v1/auth/register": () => ok({ status: "CODE_SENT" }, 202),
  "POST /api/v1/auth/verify-email": () => ok({ authRequired: false, user: { id: "00000000-0000-4000-8000-000000000000", email: "e2e@example.test" } }),
  "POST /api/v1/auth/resend-code": () => ok({ status: "CODE_SENT" }, 202),
  "POST /api/v1/auth/password/forgot": () => ok({ status: "CODE_SENT" }, 202),
  "POST /api/v1/auth/password/reset": () => ({ status: 204, body: "" }),
  "POST /api/v1/auth/logout": () => ({ status: 204, body: "" }),
  // CC-901: sem chave da Riot (como na demonstração), a aba Histórico não sincroniza.
  "POST /api/v1/history/sync": () => ok(historySync({ state: "UNAVAILABLE", reason: "RIOT_KEY_MISSING" })),
  "GET /api/v1/history/sync": () => ok(historySync({ state: "UNAVAILABLE", reason: "RIOT_KEY_MISSING" })),
  "POST /api/v1/players/lookup": () => ok(playerProfile()),
  "POST /api/v1/players/lookup-from-match": () => ok(playerProfile()),
  // CC-1202: chip do topo com o perfil da conta configurada e faixa com o catálogo.
  // Sem ícone por padrão: as telas sem rede externa continuam sem pedir nada ao CDN.
  "GET /api/v1/players/me": () => ok({ reason: null, profile: playerProfile({ id: "7a5c3f1e-0000-4000-8000-0000000000aa", gameName: "Conta Própria", profileIconId: null }) }),
  // CC-1211: por padrão o jogador não está em partida.
  "POST /api/v1/players/live": ({ body }) => ok({ state: "NOT_IN_GAME", gameName: (body as { gameName: string }).gameName, tagLine: (body as { tagLine: string }).tagLine, checkedAt: "2026-10-22T15:00:00Z", game: null }),
  "POST /api/v1/players/live/status": ({ body }) => ok({ state: "NOT_IN_GAME", gameName: (body as { gameName: string }).gameName, tagLine: (body as { tagLine: string }).tagLine, checkedAt: "2026-10-22T15:00:00Z", queueId: null, gameMode: null, startedAt: null }),
  "GET /api/v1/catalog/current": () => ok({ gamePatch: "16.20", dataDragonVersion: "16.20.1" }),
  "POST /api/v1/players/:id/sync": () => ok(historySync({ state: "SUCCEEDED", finishedAt: "2026-10-19T15:00:00Z" })),
  "GET /api/v1/players/:id/sync": () => ok(historySync({ state: "SUCCEEDED", finishedAt: "2026-10-19T15:00:00Z" })),
  "GET /api/v1/matches/:id/purge-preview": () =>
    ok({
      matchId: "DEMO-0008",
      counts: { participants: 10, timelineEvents: 40, decisionStates: 6, decisionPoints: 2, analyses: 1, learningAttempts: 1, feedback: 0 },
      datasets: [],
      models: [],
      unverifiableDatasets: 0,
      limitations: ["Modelos já treinados não desaprendem: os que usaram esta partida saem de ativo."],
    }),
  "DELETE /api/v1/matches/:id": () =>
    ok({ jobId: "9f00e000-0000-4000-8000-000000000001", status: "QUEUED", statusUrl: "/api/v1/imports/9f00e000-0000-4000-8000-000000000001" }, 202),
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
  const study = studyHandlers();
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
    const handler = (overrides[key] ?? study[key] ?? happyPath[key]) as Handler;
    const reply = handler({
      method: request.method(),
      url,
      body: request.postData() ? JSON.parse(request.postData() as string) : undefined,
    });
    if (reply.status === 0) {
      return; // HANG: a requisição fica pendente até o cliente desistir
    }
    await route.fulfill({
      status: reply.status,
      headers: { ...reply.headers, "content-type": reply.contentType ?? "application/json" },
      body: typeof reply.body === "string" ? reply.body : JSON.stringify(reply.body),
    });
  });
  return { calls, count: (key) => calls.filter((call) => call === key).length };
}

/** Sequência de respostas: a última se repete. */
export function sequence(...replies: Array<() => MockReply>): Handler {
  let index = 0;
  return () => replies[Math.min(index++, replies.length - 1)]();
}

/** Gamificação de exemplo: nível 3, sequência de 2 dias, uma missão concluída e duas conquistas. */
export function gamification(matchId: string | null = null) {
  const goal = (key: string, title: string, progress: number, target: number, done: "unlocked" | "completed") => ({
    key, title, description: `${title}.`, progress, target, [done]: progress >= target,
  });
  return {
    xp: 310, level: 3, levelXp: 60, levelTarget: 200, studiedDecisions: 7,
    streak: { current: 2, best: 4, studiedToday: true },
    achievements: [
      goal("first_study", "Primeira decisão", 1, 1, "unlocked"),
      goal("first_reflection", "Primeira reflexão", 1, 1, "unlocked"),
      goal("study_10", "Estudioso", 7, 10, "unlocked"),
      goal("streak_7", "Semana completa", 4, 7, "unlocked"),
    ],
    missions: [
      goal("study_3", "Estude 3 decisões", 3, 3, "completed"),
      goal("reflect_2", "Escreva 2 reflexões", 1, 2, "completed"),
      goal("loss_1", "Estude uma derrota", 0, 1, "completed"),
    ],
    weekStart: "2026-11-09",
    match: matchId ? { matchId, studiedDecisionIds: [DECISION_1], xp: 55 } : null,
  };
}
