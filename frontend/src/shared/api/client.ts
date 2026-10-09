import { ApiError, INVALID_RESPONSE, NETWORK_ERROR, isAbortError, parseProblem } from "./errors";
import type { components } from "./types";

/**
 * Cliente ÚNICO da API do RiftLab (CC-205). Todo tipo de requisição/resposta é um
 * alias dos tipos gerados a partir do OpenAPI (`types.ts`, `npm run generate:api-types`):
 * nada de tipo paralelo escrito à mão que possa divergir do contrato.
 */
type Schemas = components["schemas"];

export type RankedMe = Schemas["RankedMe"];
export type ImportMode = Schemas["ImportMode"];
export type ImportJobAccepted = Schemas["ImportJobAccepted"];
export type ImportJobStatus = Schemas["ImportJobStatus"];
export type ImportJobState = Schemas["ImportJobState"];
export type MatchSummary = Schemas["MatchSummary"];
export type MatchDetail = Schemas["MatchDetail"];
export type MatchParticipant = Schemas["MatchParticipant"];
export type ChampionAsset = Schemas["ChampionAsset"];
export type MatchPreparation = Schemas["MatchPreparation"];
export type PreparationStatus = Schemas["PreparationStatus"];
export type MatchPage = Schemas["CursorPage_MatchSummary_"];
export type DecisionPage = Schemas["CursorPage_DecisionStateSummary_"];
export type DecisionSummary = Schemas["DecisionStateSummary"];
export type DecisionState = Schemas["DecisionState"];
export type DecisionBudget = Schemas["DecisionBudget"];
export type DecisionReason = Schemas["DecisionReason"];
export type InventoryItem = Schemas["InventoryItem"];
export type BudgetSource = Schemas["BudgetSource"];
export type LearningAttempt = Schemas["LearningAttemptPublicState"];
export type AnalysisResult = Schemas["AnalysisResult"];
export type AnalysisCandidate = Schemas["AnalysisCandidate"];
export type ObjectivePriority = Schemas["ObjectiveForm"]["priority"];

export interface PageParams {
  cursor?: string | null;
  limit?: number;
}

interface RequestOptions {
  body?: unknown;
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export interface ApiClient {
  getRankedMe(signal?: AbortSignal): Promise<RankedMe>;
  createImport(matchId: string, mode: ImportMode, signal?: AbortSignal): Promise<ImportJobAccepted>;
  getImport(jobId: string, signal?: AbortSignal): Promise<ImportJobStatus>;
  retryImport(jobId: string, signal?: AbortSignal): Promise<ImportJobAccepted>;
  listMatches(params?: PageParams, signal?: AbortSignal): Promise<MatchPage>;
  getMatch(matchId: string, signal?: AbortSignal): Promise<MatchDetail>;
  prepareMatch(matchId: string, signal?: AbortSignal): Promise<MatchPreparation>;
  listDecisions(
    matchId: string,
    participantId: number,
    params?: PageParams,
    signal?: AbortSignal,
  ): Promise<DecisionPage>;
  getDecision(decisionId: string, signal?: AbortSignal): Promise<DecisionState>;
  createAttempt(
    decisionId: string,
    priority: ObjectivePriority,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<LearningAttempt>;
  answerAttempt(
    attemptId: string,
    answer: { itemId: number | null; skipped: boolean; rationale?: string | null },
    signal?: AbortSignal,
  ): Promise<LearningAttempt>;
  hintAttempt(attemptId: string, level: 1 | 2, signal?: AbortSignal): Promise<LearningAttempt>;
  revealAttempt(attemptId: string, signal?: AbortSignal): Promise<LearningAttempt>;
  createScenarioAnalysis(
    decisionId: string,
    priority: ObjectivePriority,
    gold: number,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<AnalysisResult>;
}

function queryString(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export function createApiClient(fetchImpl?: typeof fetch): ApiClient {
  async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
    const doFetch = fetchImpl ?? fetch;
    let response: Response;
    try {
      response = await doFetch(path, {
        method,
        signal: options.signal,
        headers: {
          accept: "application/json",
          ...(options.body === undefined ? {} : { "content-type": "application/json" }),
          ...(options.idempotencyKey ? { "Idempotency-Key": options.idempotencyKey } : {}),
        },
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
      });
    } catch (error) {
      if (isAbortError(error)) {
        throw error;
      }
      throw new ApiError(0, NETWORK_ERROR);
    }
    if (!response.ok) {
      throw await parseProblem(response);
    }
    try {
      return (await response.json()) as T;
    } catch (error) {
      if (isAbortError(error)) {
        throw error;
      }
      throw new ApiError(response.status, INVALID_RESPONSE);
    }
  }

  const enc = encodeURIComponent;

  return {
    getRankedMe: (signal) => request("GET", "/api/v1/ranked/me", { signal }),
    // Modo RIOT sem chave no servidor: 422 com code=RIOT_KEY_MISSING (tabela de mensagens).
    createImport: (matchId, mode, signal) =>
      request("POST", "/api/v1/imports", { body: { matchId, mode }, signal }),
    getImport: (jobId, signal) => request("GET", `/api/v1/imports/${enc(jobId)}`, { signal }),
    retryImport: (jobId, signal) =>
      request("POST", `/api/v1/imports/${enc(jobId)}/retry`, { signal }),
    listMatches: (params = {}, signal) =>
      request("GET", `/api/v1/matches${queryString({ cursor: params.cursor, limit: params.limit })}`, {
        signal,
      }),
    getMatch: (matchId, signal) => request("GET", `/api/v1/matches/${enc(matchId)}`, { signal }),
    prepareMatch: (matchId, signal) =>
      request("POST", `/api/v1/matches/${enc(matchId)}/preparation`, { signal }),
    listDecisions: (matchId, participantId, params = {}, signal) =>
      request(
        "GET",
        `/api/v1/matches/${enc(matchId)}/decisions${queryString({
          participantId,
          cursor: params.cursor,
          limit: params.limit,
        })}`,
        { signal },
      ),
    getDecision: (decisionId, signal) =>
      request("GET", `/api/v1/decisions/${enc(decisionId)}`, { signal }),
    createAttempt: (decisionId, priority, idempotencyKey, signal) =>
      request("POST", "/api/v1/learning-attempts", {
        body: { decisionId, objective: { priority } },
        signal,
        idempotencyKey,
      }),
    answerAttempt: (attemptId, answer, signal) =>
      request("POST", `/api/v1/learning-attempts/${enc(attemptId)}/answer`, {
        body: answer,
        signal,
      }),
    hintAttempt: (attemptId, level, signal) =>
      request("POST", `/api/v1/learning-attempts/${enc(attemptId)}/hint`, {
        body: { level },
        signal,
      }),
    createScenarioAnalysis: (decisionId, priority, gold, idempotencyKey, signal) =>
      request("POST", "/api/v1/analyses", {
        body: { decisionId, objective: { priority }, budget: { gold, source: "USER_PROVIDED" } },
        signal,
        idempotencyKey,
      }),
    revealAttempt: (attemptId, signal) =>
      request("POST", `/api/v1/learning-attempts/${enc(attemptId)}/reveal`, {
        body: { explicit: true },
        signal,
      }),
  };
}

/** Instância padrão: usa o `fetch` global no momento da chamada (mockável nos testes). */
export const api: ApiClient = createApiClient();
