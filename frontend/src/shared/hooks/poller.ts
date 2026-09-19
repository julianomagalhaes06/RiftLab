import { isAbortError, isApiError } from "../api/errors";

/**
 * Motor de polling (CC-205), independente de React para ser testável com timers
 * falsos. Regras:
 * - busca imediata, depois espera crescente (backoff exponencial) até um teto;
 * - para em estado terminal (`isTerminal`), sem nenhuma requisição depois;
 * - respeita `retryAt`: nunca consulta antes do instante em que algo pode mudar
 *   (limitado por `retryAtPollCeilingMs`, para a tela continuar viva);
 * - AbortController por consulta: `stop()` cancela a requisição em voo e o timer;
 * - erro definitivo (4xx) para na hora; erro transitório (rede/5xx) tenta de novo
 *   com backoff até `maxConsecutiveErrors`;
 * - teto de duração (`maxDurationMs`): depois dele para com fase "stalled" (o usuário
 *   decide continuar).
 */
export type PollerPhase = "polling" | "done" | "stalled" | "failed";

export interface PollerState<T> {
  phase: PollerPhase;
  data: T | null;
  error: unknown;
  /** Consultas concluídas com sucesso. */
  attempts: number;
  /** Instante (epoch ms) da próxima consulta agendada; null se parado. */
  nextPollAt: number | null;
}

export interface PollerConfig<T> {
  fetcher: (signal: AbortSignal) => Promise<T>;
  isTerminal: (value: T) => boolean;
  getRetryAt?: (value: T) => string | null | undefined;
  onUpdate: (state: PollerState<T>) => void;
  initialDelayMs?: number;
  maxDelayMs?: number;
  factor?: number;
  maxDurationMs?: number;
  maxConsecutiveErrors?: number;
  retryAtPollCeilingMs?: number;
  now?: () => number;
  shouldRetryError?: (error: unknown) => boolean;
}

export const POLLING_DEFAULTS = {
  initialDelayMs: 1_000,
  maxDelayMs: 10_000,
  factor: 1.6,
  maxDurationMs: 10 * 60_000,
  maxConsecutiveErrors: 5,
  retryAtPollCeilingMs: 60_000,
} as const;

/** Definitivo (não adianta repetir): resposta 4xx da API. Rede e 5xx podem passar. */
export function defaultShouldRetryError(error: unknown): boolean {
  return !(isApiError(error) && error.status >= 400 && error.status < 500);
}

export function backoffDelay(
  attemptIndex: number,
  initialDelayMs: number,
  factor: number,
  maxDelayMs: number,
): number {
  return Math.min(maxDelayMs, Math.round(initialDelayMs * factor ** attemptIndex));
}

/** Próxima espera: backoff, nunca antes de `retryAt` (até o teto de consulta). */
export function nextDelay(
  attemptIndex: number,
  retryAt: string | null | undefined,
  nowMs: number,
  cfg: Required<
    Pick<PollerConfig<unknown>, "initialDelayMs" | "maxDelayMs" | "factor" | "retryAtPollCeilingMs">
  >,
): number {
  const backoff = backoffDelay(attemptIndex, cfg.initialDelayMs, cfg.factor, cfg.maxDelayMs);
  if (!retryAt) {
    return backoff;
  }
  const at = Date.parse(retryAt);
  if (Number.isNaN(at) || at <= nowMs) {
    return backoff;
  }
  // +250 ms: a consulta cai DEPOIS do retryAt, quando o worker já pode ter agido.
  return Math.max(backoff, Math.min(at - nowMs + 250, cfg.retryAtPollCeilingMs));
}

export function startPoller<T>(config: PollerConfig<T>): { stop(): void } {
  const initialDelayMs = config.initialDelayMs ?? POLLING_DEFAULTS.initialDelayMs;
  const maxDelayMs = config.maxDelayMs ?? POLLING_DEFAULTS.maxDelayMs;
  const factor = config.factor ?? POLLING_DEFAULTS.factor;
  const maxDurationMs = config.maxDurationMs ?? POLLING_DEFAULTS.maxDurationMs;
  const maxConsecutiveErrors =
    config.maxConsecutiveErrors ?? POLLING_DEFAULTS.maxConsecutiveErrors;
  const retryAtPollCeilingMs = config.retryAtPollCeilingMs ?? POLLING_DEFAULTS.retryAtPollCeilingMs;
  const now = config.now ?? Date.now;
  const shouldRetryError = config.shouldRetryError ?? defaultShouldRetryError;

  const startedAt = now();
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let controller: AbortController | null = null;
  let state: PollerState<T> = {
    phase: "polling",
    data: null,
    error: null,
    attempts: 0,
    nextPollAt: null,
  };
  let pollIndex = 0;
  let consecutiveErrors = 0;

  function emit(patch: Partial<PollerState<T>>): void {
    state = { ...state, ...patch };
    if (!stopped) {
      config.onUpdate(state);
    }
  }

  function schedule(delayMs: number): void {
    if (now() - startedAt + delayMs > maxDurationMs) {
      emit({ phase: "stalled", nextPollAt: null });
      return;
    }
    emit({ nextPollAt: now() + delayMs });
    timer = setTimeout(() => void tick(), delayMs);
  }

  async function tick(): Promise<void> {
    if (stopped) {
      return;
    }
    timer = null;
    controller = new AbortController();
    try {
      const value = await config.fetcher(controller.signal);
      if (stopped) {
        return;
      }
      consecutiveErrors = 0;
      const attempts = state.attempts + 1;
      if (config.isTerminal(value)) {
        emit({ phase: "done", data: value, error: null, attempts, nextPollAt: null });
        return;
      }
      emit({ data: value, error: null, attempts, phase: "polling" });
      schedule(
        nextDelay(pollIndex++, config.getRetryAt?.(value), now(), {
          initialDelayMs,
          maxDelayMs,
          factor,
          retryAtPollCeilingMs,
        }),
      );
    } catch (error) {
      if (stopped || isAbortError(error)) {
        return;
      }
      consecutiveErrors += 1;
      if (!shouldRetryError(error) || consecutiveErrors >= maxConsecutiveErrors) {
        emit({ phase: "failed", error, nextPollAt: null });
        return;
      }
      emit({ error });
      schedule(backoffDelay(pollIndex++, initialDelayMs, factor, maxDelayMs));
    }
  }

  void tick();

  return {
    stop() {
      stopped = true;
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      controller?.abort();
    },
  };
}
