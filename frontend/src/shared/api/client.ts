import { ApiError, INVALID_RESPONSE, NETWORK_ERROR, isAbortError, parseProblem } from "./errors";
import type { components } from "./types";

/**
 * Cliente ÚNICO da API do RiftLab (CC-205). Todo tipo de requisição/resposta é um
 * alias dos tipos gerados a partir do OpenAPI (`types.ts`, `npm run generate:api-types`):
 * nada de tipo paralelo escrito à mão que possa divergir do contrato.
 */
type Schemas = components["schemas"];

export type Dashboard = Schemas["Dashboard"];
export type RankedMe = Schemas["RankedMe"];
export type RankedQueue = Schemas["Position"]["queue"];
export type ImportMode = Schemas["ImportMode"];
export type ImportJobAccepted = Schemas["ImportJobAccepted"];
export type ImportJobStatus = Schemas["ImportJobStatus"];
export type ImportJobState = Schemas["ImportJobState"];
export type PurgePreview = Schemas["PurgePreview"];
export type MatchSummary = Schemas["MatchSummary"];
export type MatchDetail = Schemas["MatchDetail"];
export type MatchParticipant = Schemas["MatchParticipant"];
export type MatchTimeline = Schemas["MatchTimeline"];
export type TimelineItemEvent = Schemas["TimelineItemEvent"];
export type TimelineObjective = Schemas["TimelineObjective"];
export type ChampionAsset = Schemas["ChampionAsset"];
export type ChampionPatterns = Schemas["ChampionPatterns"];
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
export type ObjectiveForm = Schemas["ObjectiveForm"];
export type ObjectiveInterpretation = Schemas["ObjectiveInterpretResult"];
export type FeedbackRequest = Schemas["FeedbackRequest"];
export type FeedbackResult = Schemas["FeedbackResult"];
export type LearningProgress = Schemas["LearningProgress"];
export type AttemptMode = NonNullable<Schemas["LearningAttemptCreateRequest"]["mode"]>;

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
  getDashboard(params: { origin: "REAL" | "DEMO"; window: number }, signal?: AbortSignal): Promise<Dashboard>;
  getChampionPatterns(params: { championId?: number; patch?: string; phase?: "first" | "second" | "sequence" }, signal?: AbortSignal): Promise<ChampionPatterns>;
  getRankedMe(signal?: AbortSignal, queue?: RankedQueue): Promise<RankedMe>;
  createImport(matchId: string, mode: ImportMode, signal?: AbortSignal): Promise<ImportJobAccepted>;
  getImport(jobId: string, signal?: AbortSignal): Promise<ImportJobStatus>;
  retryImport(jobId: string, signal?: AbortSignal): Promise<ImportJobAccepted>;
  listMatches(params?: PageParams, signal?: AbortSignal): Promise<MatchPage>;
  getMatch(matchId: string, signal?: AbortSignal): Promise<MatchDetail>;
  getMatchTimeline(matchId: string, signal?: AbortSignal): Promise<MatchTimeline>;
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
    objective: ObjectiveForm,
    idempotencyKey: string,
    signal?: AbortSignal,
    mode?: AttemptMode,
  ): Promise<LearningAttempt>;
  reflectAttempt(attemptId: string, reflection: string, signal?: AbortSignal): Promise<LearningAttempt>;
  getProgress(signal?: AbortSignal): Promise<LearningProgress>;
  submitFeedback(
    analysisId: string,
    feedback: FeedbackRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<FeedbackResult>;
  answerAttempt(
    attemptId: string,
    answer: { itemId: number | null; skipped: boolean; rationale?: string | null },
    signal?: AbortSignal,
  ): Promise<LearningAttempt>;
  hintAttempt(attemptId: string, level: 1 | 2, signal?: AbortSignal): Promise<LearningAttempt>;
  revealAttempt(attemptId: string, signal?: AbortSignal): Promise<LearningAttempt>;
  createScenarioAnalysis(
    decisionId: string,
    objective: ObjectiveForm,
    gold: number,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<AnalysisResult>;
  createObjectiveAnalysis(
    decisionId: string,
    objective: ObjectiveForm,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<AnalysisResult>;
  interpretObjective(text: string, signal?: AbortSignal): Promise<ObjectiveInterpretation>;
  getPurgePreview(matchId: string, signal?: AbortSignal): Promise<PurgePreview>;
  /** CC-603: expurgo com confirmação explícita (o próprio id); devolve o job PURGE. */
  purgeMatch(matchId: string, signal?: AbortSignal): Promise<ImportJobAccepted>;
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
    getChampionPatterns: (params, signal) => request("GET", `/api/v1/champions/patterns${queryString(params)}`, { signal }),
    getDashboard: (params, signal) => request("GET", `/api/v1/dashboard?origin=${params.origin}&window=${params.window}`, { signal }),
    getRankedMe: (signal, queue) => request("GET", `/api/v1/ranked/me${queryString({ queue })}`, { signal }),
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
    getMatchTimeline: (matchId, signal) =>
      request("GET", `/api/v1/matches/${enc(matchId)}/timeline`, { signal }),
    getPurgePreview: (matchId, signal) =>
      request("GET", `/api/v1/matches/${enc(matchId)}/purge-preview`, { signal }),
    purgeMatch: (matchId, signal) =>
      request("DELETE", `/api/v1/matches/${enc(matchId)}${queryString({ confirm: matchId })}`, { signal }),
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
    createAttempt: (decisionId, objective, idempotencyKey, signal, mode = "STUDY") =>
      request("POST", "/api/v1/learning-attempts", {
        body: { decisionId, objective, mode },
        signal,
        idempotencyKey,
      }),
    reflectAttempt: (attemptId, reflection, signal) =>
      request("POST", `/api/v1/learning-attempts/${enc(attemptId)}/reflection`, {
        body: { reflection },
        signal,
      }),
    getProgress: (signal) => request("GET", "/api/v1/learning-attempts/progress", { signal }),
    submitFeedback: (analysisId, feedback, idempotencyKey, signal) =>
      request("POST", `/api/v1/analyses/${enc(analysisId)}/feedback`, {
        body: feedback,
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
    createScenarioAnalysis: (decisionId, objective, gold, idempotencyKey, signal) =>
      request("POST", "/api/v1/analyses", {
        body: { decisionId, objective, budget: { gold, source: "USER_PROVIDED" } },
        signal,
        idempotencyKey,
      }),
    createObjectiveAnalysis: (decisionId, objective, idempotencyKey, signal) =>
      request("POST", "/api/v1/analyses", { body: { decisionId, objective }, signal, idempotencyKey }),
    // CC-504: o texto é dado; o servidor devolve INTERPRETED (para confirmar) ou NEEDS_INPUT.
    interpretObjective: (text, signal) =>
      request("POST", "/api/v1/objectives/interpret", { body: { text }, signal }),
    revealAttempt: (attemptId, signal) =>
      request("POST", `/api/v1/learning-attempts/${enc(attemptId)}/reveal`, {
        body: { explicit: true },
        signal,
      }),
  };
}

/** CC-505: link de download do relatório (JSON ou HTML autossuficiente). */
export function reportUrl(analysisId: string, format: "json" | "html", attemptId?: string | null): string {
  const query = queryString({ format, attemptId: attemptId ?? undefined });
  return `/api/v1/analyses/${encodeURIComponent(analysisId)}/report${query}`;
}

/** Instância padrão: usa o `fetch` global no momento da chamada (mockável nos testes). */
export const api: ApiClient = createApiClient();
