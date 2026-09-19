import { useCallback, useEffect, useRef, useState } from "react";
import { startPoller, type PollerConfig, type PollerState } from "./poller";

export interface UsePollingOptions<T>
  extends Pick<
    PollerConfig<T>,
    | "fetcher"
    | "isTerminal"
    | "getRetryAt"
    | "initialDelayMs"
    | "maxDelayMs"
    | "factor"
    | "maxDurationMs"
    | "maxConsecutiveErrors"
    | "retryAtPollCeilingMs"
  > {
  /** Muda quando o alvo muda (ex.: outro jobId/matchId): reinicia o polling do zero. */
  resetKey: string;
  enabled?: boolean;
}

export interface UsePollingResult<T> extends PollerState<T> {
  /** Reinicia o acompanhamento (depois de uma nova tentativa, de "stalled" ou "failed"). */
  restart(): void;
}

const INITIAL_STATE = {
  phase: "polling",
  data: null,
  error: null,
  attempts: 0,
  nextPollAt: null,
} as const;

/**
 * Polling com backoff/teto/retryAt e cancelamento (ver poller.ts). Cancela a
 * requisição em voo e o timer ao desmontar, ao trocar `resetKey` ou ao desabilitar —
 * nunca continua consultando depois de sair da tela ou de um estado terminal.
 */
export function usePolling<T>(options: UsePollingOptions<T>): UsePollingResult<T> {
  const { resetKey, enabled = true } = options;
  const latest = useRef(options);
  useEffect(() => {
    latest.current = options;
  });

  const [tracked, setTracked] = useState<{ key: string; state: PollerState<T> }>({
    key: resetKey,
    state: INITIAL_STATE,
  });
  const [restarts, setRestarts] = useState(0);

  useEffect(() => {
    if (!enabled) {
      return;
    }
    const current = latest.current;
    const poller = startPoller<T>({
      fetcher: (signal) => latest.current.fetcher(signal),
      isTerminal: (value) => latest.current.isTerminal(value),
      getRetryAt: (value) => latest.current.getRetryAt?.(value),
      initialDelayMs: current.initialDelayMs,
      maxDelayMs: current.maxDelayMs,
      factor: current.factor,
      maxDurationMs: current.maxDurationMs,
      maxConsecutiveErrors: current.maxConsecutiveErrors,
      retryAtPollCeilingMs: current.retryAtPollCeilingMs,
      onUpdate: (state) => setTracked({ key: resetKey, state }),
    });
    return () => poller.stop();
  }, [resetKey, enabled, restarts]);

  const restart = useCallback(() => {
    setTracked((previous) => ({
      key: previous.key,
      state: { ...previous.state, phase: "polling", error: null },
    }));
    setRestarts((count) => count + 1);
  }, []);

  // Estado de outro alvo (resetKey mudou e ainda não houve resposta) nunca vaza.
  const state = tracked.key === resetKey ? tracked.state : INITIAL_STATE;
  return { ...state, restart };
}
