import { useCallback, useEffect, useRef, useState } from "react";
import { api, type HistorySyncStatus } from "../../shared/api/client";
import { active, syncMessage } from "./syncMessage";

/**
 * CC-901: ao abrir o Histórico, pede uma sincronização com a Riot (só a conta própria).
 * O servidor só agenda; o worker lista as partidas recentes e enfileira as novas. Enquanto
 * a execução está ativa, consulta o estado. Com `latestIds`, as partidas novas entram de uma
 * vez, como nos sites de estatísticas: a tela mostra a espera (`onPendingChange`) e confere em
 * segundo plano quantas já chegaram, comparando com o que a lista mostrava (`knownIds`); quando
 * todas chegam (ou o tempo de espera acaba), recarrega a lista uma só vez. Sem `latestIds`,
 * recarrega a lista algumas vezes, sem piscar.
 */

const ACTIVE_POLL_MS = 2000;
// O worker importa várias partidas em paralelo (um histórico de 20 em poucos segundos): a lista
// recarrega a cada 2,5 s por um minuto, tempo de sobra também para a preparação das revisões.
const REFRESH_EVERY_MS = 2500;
const REFRESH_TIMES = 24;
const ARRIVAL_POLL_MS = 2000;
const ARRIVAL_MAX_WAIT_MS = 60000;

/** Espera visível na lista: sincronizando (expected nulo) ou trazendo N partidas novas. */
export interface SyncPending {
  expected: number | null;
}

interface SyncCalls {
  /** `manual`: pedido pelo botão Atualizar (não pela abertura da tela). */
  request: (signal?: AbortSignal, manual?: boolean) => Promise<HistorySyncStatus>;
  status: (signal?: AbortSignal) => Promise<HistorySyncStatus>;
}

const OWN_ACCOUNT: SyncCalls = {
  request: (signal) => api.requestHistorySync(signal),
  status: (signal) => api.getHistorySync(signal),
};

/** `calls` troca a conta configurada por outro alvo, como o perfil buscado (CC-1102).
 * `latestIds`: ids da primeira página no servidor; `knownIds`: os que a lista já mostra. */
export function HistorySync({
  onNewMatches,
  calls = OWN_ACCOUNT,
  latestIds,
  knownIds,
  onPendingChange,
}: {
  onNewMatches: () => void;
  calls?: SyncCalls;
  latestIds?: (signal?: AbortSignal) => Promise<string[]>;
  knownIds?: () => string[];
  onPendingChange?: (pending: SyncPending | null) => void;
}) {
  const [status, setStatus] = useState<HistorySyncStatus | null>(null);
  const [failedRequest, setFailedRequest] = useState(false);
  const [requesting, setRequesting] = useState(true);
  const [now, setNow] = useState(() => Date.now());
  const refreshes = useRef(0);
  const onNew = useRef(onNewMatches);
  const onPending = useRef(onPendingChange);
  const idsOf = useRef(latestIds);
  const known = useRef(knownIds);
  const [waitingImports, setWaitingImports] = useState(false);
  useEffect(() => {
    onNew.current = onNewMatches;
    onPending.current = onPendingChange;
    idsOf.current = latestIds;
    known.current = knownIds;
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

  const arriving = status?.state === "SUCCEEDED" && !!status.newEnqueued && status.reason !== "COOLDOWN";

  // Com `latestIds`: espera todas chegarem e recarrega a lista uma só vez.
  useEffect(() => {
    if (!arriving || !status?.newEnqueued || !idsOf.current) return;
    const expected = Math.min(status.newEnqueued, 20); // primeira página da lista
    // O que a lista mostrava antes das novas (carregada ao abrir a tela).
    const before = new Set(known.current?.() ?? []);
    const controller = new AbortController();
    const started = Date.now();
    let timer: number | undefined;
    setWaitingImports(true);
    const check = async () => {
      const ids = await idsOf.current!(controller.signal).catch(() => null);
      if (controller.signal.aborted) return;
      const arrived = ids ? ids.filter((id) => !before.has(id)).length : 0;
      if (arrived >= expected || Date.now() - started >= ARRIVAL_MAX_WAIT_MS) {
        setWaitingImports(false);
        onNew.current();
        return;
      }
      timer = window.setTimeout(() => void check(), ARRIVAL_POLL_MS);
    };
    timer = window.setTimeout(() => void check(), ARRIVAL_POLL_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [arriving, status]);

  // Espera visível na lista: sincronizando, ou trazendo as partidas novas.
  const pendingExpected = waitingImports ? Math.min(status?.newEnqueued ?? 0, 20) : null;
  const syncing = active(status) || (requesting && status === null);
  useEffect(() => {
    onPending.current?.(waitingImports ? { expected: pendingExpected } : syncing ? { expected: null } : null);
  }, [waitingImports, syncing, pendingExpected]);

  // Sem `latestIds`: recarrega a lista enquanto as importações terminam.
  useEffect(() => {
    if (!arriving || idsOf.current) return;
    const timer = window.setInterval(() => {
      refreshes.current += 1;
      onNew.current();
      if (refreshes.current >= REFRESH_TIMES) window.clearInterval(timer);
    }, REFRESH_EVERY_MS);
    return () => window.clearInterval(timer);
  }, [arriving, status]);

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
  const tone = status?.state === "FAILED" || failedRequest ? "history-sync surface history-sync--warning" : "history-sync surface";

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
