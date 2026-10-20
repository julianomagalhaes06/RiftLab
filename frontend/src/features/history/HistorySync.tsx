import { useCallback, useEffect, useRef, useState } from "react";
import { api, type HistorySyncStatus } from "../../shared/api/client";
import { active, syncMessage } from "./syncMessage";

/**
 * CC-901: ao abrir o Histórico, pede uma sincronização com a Riot (só a conta própria).
 * O servidor só agenda; o worker lista as partidas recentes e enfileira as novas. Enquanto
 * a execução está ativa, consulta o estado; quando ela traz partidas novas, recarrega a
 * lista algumas vezes, sem piscar, para elas aparecerem conforme a importação termina.
 */

const ACTIVE_POLL_MS = 2000;
const REFRESH_EVERY_MS = 5000;
const REFRESH_TIMES = 12;

interface SyncCalls {
  /** `manual`: pedido pelo botão Atualizar (não pela abertura da tela). */
  request: (signal?: AbortSignal, manual?: boolean) => Promise<HistorySyncStatus>;
  status: (signal?: AbortSignal) => Promise<HistorySyncStatus>;
}

const OWN_ACCOUNT: SyncCalls = {
  request: (signal) => api.requestHistorySync(signal),
  status: (signal) => api.getHistorySync(signal),
};

/** `calls` troca a conta configurada por outro alvo, como o perfil buscado (CC-1102). */
export function HistorySync({ onNewMatches, calls = OWN_ACCOUNT }: { onNewMatches: () => void; calls?: SyncCalls }) {
  const [status, setStatus] = useState<HistorySyncStatus | null>(null);
  const [failedRequest, setFailedRequest] = useState(false);
  const [requesting, setRequesting] = useState(true);
  const [now, setNow] = useState(() => Date.now());
  const refreshes = useRef(0);
  const onNew = useRef(onNewMatches);
  useEffect(() => {
    onNew.current = onNewMatches;
  });

  const send = useCallback((signal?: AbortSignal, manual = false) => {
    calls
      .request(signal, manual)
      .then((next) => {
        refreshes.current = 0;
        setNow(Date.now());
        setStatus(next);
      })
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) setFailedRequest(true);
      })
      .finally(() => setRequesting(false));
  }, [calls]);

  const ask = () => {
    setRequesting(true);
    setFailedRequest(false);
    send(undefined, true);
  };

  // Uma vez por visita à aba.
  useEffect(() => {
    const controller = new AbortController();
    send(controller.signal);
    return () => controller.abort();
  }, [send]);

  // Execução ativa: consulta o estado até terminar.
  useEffect(() => {
    if (!active(status)) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      calls
        .status(controller.signal)
        .then((next) => {
          setNow(Date.now());
          setStatus(next);
        })
        .catch(() => undefined);
    }, ACTIVE_POLL_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [status, calls]);

  // Partidas novas enfileiradas: recarrega a lista enquanto as importações terminam.
  useEffect(() => {
    if (status?.state !== "SUCCEEDED" || !status.newEnqueued || status.reason === "COOLDOWN") return;
    const timer = window.setInterval(() => {
      refreshes.current += 1;
      onNew.current();
      if (refreshes.current >= REFRESH_TIMES) window.clearInterval(timer);
    }, REFRESH_EVERY_MS);
    return () => window.clearInterval(timer);
  }, [status]);

  const next = status?.nextAllowedAt ? new Date(status.nextAllowedAt).getTime() : null;
  const waiting = next !== null && next > now;
  // Reabilita o botão quando o intervalo mínimo termina.
  useEffect(() => {
    if (next === null || next <= now) return;
    const timer = window.setTimeout(() => setNow(Date.now()), next - now + 50);
    return () => window.clearTimeout(timer);
  }, [next, now]);
  const unavailable = status?.state === "UNAVAILABLE";
  const disabled = requesting || active(status) || waiting || unavailable;
  const tone = status?.state === "FAILED" || failedRequest ? "history-sync history-sync--warning" : "history-sync";

  return (
    <section className={tone} aria-label="Atualização do histórico com a Riot">
      <p role="status" aria-live="polite" aria-busy={active(status) || requesting}>
        {syncMessage(status, failedRequest)}
      </p>
      {!unavailable ? (
        <button type="button" className="button button--secondary" onClick={ask} disabled={disabled}>
          {active(status) || requesting ? "Atualizando…" : "Atualizar"}
        </button>
      ) : null}
    </section>
  );
}
