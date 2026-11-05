import { ApiError, INVALID_RESPONSE, NETWORK_ERROR, isAbortError, parseProblem } from "./errors";
import type { components, paths } from "./types";

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
export type MatchReview = Schemas["MatchReview"];
export type ReviewPurchase = Schemas["ReviewPurchase"];
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
export type AnalysisReferenceSource = Schemas["AnalysisReferenceSource"];
/** Fila do Painel (CC-904): sem valor, o servidor usa a da partida própria mais recente. */
export type DashboardQueueParam = "RANKED_SOLO_5x5" | "RANKED_FLEX_SR";
export type HistorySyncStatus = Schemas["HistorySyncStatus"];
export type SessionState = Schemas["SessionState"];
export type SessionUser = Schemas["SessionUser"];
export type RiotLinkState = Schemas["RiotLinkState"];

/** Evento global: uma resposta 401 AUTH_REQUIRED pede para a interface rever a sessão. */
export const AUTH_REQUIRED_EVENT = "riftlab:auth-required";
export type PlayerProfile = Schemas["PlayerProfile"];
export type CatalogCurrent = Schemas["CatalogCurrent"];
export type OwnPlayer = Schemas["OwnPlayer"];
export type LiveGame = Schemas["LiveGame"];
export type LiveParticipant = Schemas["LiveParticipant"];
export type PlayerLiveStatus = Schemas["PlayerLiveStatus"];
export type PlayerRank = Schemas["PlayerRank"];
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

export interface MatchListParams extends PageParams {
  scope?: NonNullable<paths["/api/v1/matches"]["get"]["parameters"]["query"]>["scope"];
  /** Perfil buscado por Riot ID (CC-1101): só as partidas dele. */
  player?: string | null;
}

interface RequestOptions {
  body?: unknown;
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export interface ApiClient {
  getDashboard(params: { origin: "REAL" | "DEMO"; window: number; queue?: DashboardQueueParam }, signal?: AbortSignal): Promise<Dashboard>;
  getChampionPatterns(params: { championId?: number; patch?: string; phase?: "first" | "second" | "sequence" }, signal?: AbortSignal): Promise<ChampionPatterns>;
  getRankedMe(signal?: AbortSignal, queue?: RankedQueue): Promise<RankedMe>;
  getHistorySync(signal?: AbortSignal): Promise<HistorySyncStatus>;
  requestHistorySync(signal?: AbortSignal): Promise<HistorySyncStatus>;
  lookupPlayer(gameName: string, tagLine: string, refresh?: boolean, signal?: AbortSignal): Promise<PlayerProfile>;
  /** Perfil da conta configurada; sem perfil, `profile` nulo e o motivo (CC-1202). */
  getOwnPlayer(signal?: AbortSignal): Promise<OwnPlayer>;
  /** Contas locais (Sprint 2, #10): sessão por cookie HttpOnly. */
  getAuthSession(signal?: AbortSignal): Promise<SessionState>;
  register(username: string, password: string, signal?: AbortSignal): Promise<SessionState>;
  login(username: string, password: string, signal?: AbortSignal): Promise<SessionState>;
  logout(signal?: AbortSignal): Promise<void>;
  /** Visitante (#14): sessão curta, só a demonstração. */
  enterAsGuest(signal?: AbortSignal): Promise<SessionState>;
  /** Vínculo da conta Riot pelo desafio do ícone (#12). */
  getRiotLink(signal?: AbortSignal): Promise<RiotLinkState>;
  startRiotLink(gameName: string, tagLine: string, signal?: AbortSignal): Promise<RiotLinkState>;
  verifyRiotLink(signal?: AbortSignal): Promise<RiotLinkState>;
  unlinkRiot(signal?: AbortSignal): Promise<void>;
  getCatalogCurrent(signal?: AbortSignal): Promise<CatalogCurrent>;
  /** Partida ao vivo do Riot ID (CC-1211); o Riot ID vai no corpo, fora dos logs. */
  getLiveGame(gameName: string, tagLine: string, signal?: AbortSignal): Promise<LiveGame>;
  /** Está jogando agora? Só conta e partida, 2 requisições (CC-1213). */
  getLiveStatus(gameName: string, tagLine: string, signal?: AbortSignal): Promise<PlayerLiveStatus>;
  getPlayerSync(playerId: string, signal?: AbortSignal): Promise<HistorySyncStatus>;
  /** O banco guarda só o HMAC do Riot ID (CC-1103): o pedido traz o Riot ID do perfil. */
  requestPlayerSync(playerId: string, gameName: string, tagLine: string, signal?: AbortSignal): Promise<HistorySyncStatus>;
  createImport(matchId: string, mode: ImportMode, signal?: AbortSignal): Promise<ImportJobAccepted>;
  getImport(jobId: string, signal?: AbortSignal): Promise<ImportJobStatus>;
  retryImport(jobId: string, signal?: AbortSignal): Promise<ImportJobAccepted>;
  listMatches(params?: MatchListParams, signal?: AbortSignal): Promise<MatchPage>;
  getMatch(matchId: string, signal?: AbortSignal): Promise<MatchDetail>;
  getMatchTimeline(matchId: string, signal?: AbortSignal): Promise<MatchTimeline>;
  getMatchReview(matchId: string, participantId: number, signal?: AbortSignal): Promise<MatchReview>;
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
          // Anti-CSRF (#10): página de outro site não consegue enviar este cabeçalho.
          "X-RiftLab-Request": "1",
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
      const problem = await parseProblem(response);
      if (problem.status === 401 && problem.code === "AUTH_REQUIRED" && typeof window !== "undefined") {
        window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
      }
      throw problem;
    }
    if (response.status === 204) {
      return undefined as T;
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
    getDashboard: (params, signal) =>
      request("GET", `/api/v1/dashboard?origin=${params.origin}&window=${params.window}${params.queue ? `&queue=${params.queue}` : ""}`, { signal }),
    getRankedMe: (signal, queue) => request("GET", `/api/v1/ranked/me${queryString({ queue })}`, { signal }),
    // CC-901: o POST só agenda; a rede com a Riot roda no worker.
    getHistorySync: (signal) => request("GET", "/api/v1/history/sync", { signal }),
    requestHistorySync: (signal) => request("POST", "/api/v1/history/sync", { signal }),
    lookupPlayer: (gameName, tagLine, refresh = false, signal) =>
      request("POST", "/api/v1/players/lookup", { body: { gameName, tagLine, refresh }, signal }),
    getOwnPlayer: (signal) => request("GET", "/api/v1/players/me", { signal }),
    getAuthSession: (signal) => request("GET", "/api/v1/auth/session", { signal }),
    register: (username, password, signal) =>
      request("POST", "/api/v1/auth/register", { body: { username, password }, signal }),
    login: (username, password, signal) =>
      request("POST", "/api/v1/auth/login", { body: { username, password }, signal }),
    logout: (signal) => request("POST", "/api/v1/auth/logout", { signal }),
    enterAsGuest: (signal) => request("POST", "/api/v1/auth/guest", { signal }),
    getRiotLink: (signal) => request("GET", "/api/v1/riot-link", { signal }),
    startRiotLink: (gameName, tagLine, signal) =>
      request("POST", "/api/v1/riot-link/challenge", { body: { gameName, tagLine }, signal }),
    verifyRiotLink: (signal) => request("POST", "/api/v1/riot-link/verify", { signal }),
    unlinkRiot: (signal) => request("DELETE", "/api/v1/riot-link", { signal }),
    getCatalogCurrent: (signal) => request("GET", "/api/v1/catalog/current", { signal }),
    getLiveGame: (gameName, tagLine, signal) =>
      request("POST", "/api/v1/players/live", { body: { gameName, tagLine }, signal }),
    getLiveStatus: (gameName, tagLine, signal) =>
      request("POST", "/api/v1/players/live/status", { body: { gameName, tagLine }, signal }),
    getPlayerSync: (playerId, signal) => request("GET", `/api/v1/players/${enc(playerId)}/sync`, { signal }),
    requestPlayerSync: (playerId, gameName, tagLine, signal) =>
      request("POST", `/api/v1/players/${enc(playerId)}/sync`, { body: { gameName, tagLine }, signal }),
    // Modo RIOT sem chave no servidor: 422 com code=RIOT_KEY_MISSING (tabela de mensagens).
    createImport: (matchId, mode, signal) =>
      request("POST", "/api/v1/imports", { body: { matchId, mode }, signal }),
    getImport: (jobId, signal) => request("GET", `/api/v1/imports/${enc(jobId)}`, { signal }),
    retryImport: (jobId, signal) =>
      request("POST", `/api/v1/imports/${enc(jobId)}/retry`, { signal }),
    listMatches: (params = {}, signal) =>
      request("GET", `/api/v1/matches${queryString({ cursor: params.cursor, limit: params.limit, scope: params.scope, player: params.player })}`, {
        signal,
      }),
    getMatch: (matchId, signal) => request("GET", `/api/v1/matches/${enc(matchId)}`, { signal }),
    getMatchTimeline: (matchId, signal) =>
      request("GET", `/api/v1/matches/${enc(matchId)}/timeline`, { signal }),
    getMatchReview: (matchId, participantId, signal) =>
      request("GET", `/api/v1/matches/${enc(matchId)}/review${queryString({ participantId })}`, { signal }),
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
