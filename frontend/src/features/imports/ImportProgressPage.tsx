import { useEffect, useState } from "react";
import {
  api,
  type ImportJobState,
  type ImportJobStatus,
} from "../../shared/api/client";
import { isAbortError, isApiError } from "../../shared/api/errors";
import { useAbortScope } from "../../shared/hooks/useAbortScope";
import { usePolling } from "../../shared/hooks/usePolling";
import { useRouter } from "../../shared/router/context";
import { Link } from "../../shared/router/Link";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";

const STATE_LABEL: Readonly<Record<ImportJobState, string>> = {
  QUEUED: "Na fila para importar…",
  RUNNING: "Importando a partida…",
  RETRY_WAIT: "Nova tentativa agendada",
  SUCCEEDED: "Importação concluída",
  FAILED: "A importação falhou",
};

function isTerminal(job: ImportJobStatus): boolean {
  return job.status === "SUCCEEDED" || job.status === "FAILED";
}

function formatClock(iso: string): string | null {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleTimeString("pt-BR");
}

/** Progresso de uma importação (/imports/:jobId): polling até SUCCEEDED/FAILED. */
export function ImportProgressPage({ jobId }: { jobId: string }) {
  const { navigate } = useRouter();
  const newController = useAbortScope();
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<unknown>(null);

  const polling = usePolling<ImportJobStatus>({
    resetKey: jobId,
    fetcher: (signal) => api.getImport(jobId, signal),
    isTerminal,
    getRetryAt: (job) => job.retryAt,
  });
  const job = polling.data;
  const matchId = job?.status === "SUCCEEDED" ? job.matchId : null;

  useEffect(() => {
    if (matchId) {
      // replace: voltar não devolve o usuário a esta tela de progresso já concluída.
      navigate({ name: "match", matchId, participantId: null }, { replace: true });
    }
  }, [matchId, navigate]);

  async function handleRetry() {
    setRetryError(null);
    setRetrying(true);
    const controller = newController();
    try {
      const accepted = await api.retryImport(jobId, controller.signal);
      navigate({ name: "import", jobId: accepted.jobId }, { replace: true });
    } catch (error) {
      if (!isAbortError(error)) {
        setRetryError(error);
        setRetrying(false);
      }
    }
  }

  const notFound = polling.phase === "failed" && isApiError(polling.error) && polling.error.status === 404;
  const retryAtText = job?.retryAt ? formatClock(job.retryAt) : null;

  return (
    <section className="section" aria-labelledby="import-heading">
      <h2 id="import-heading">Importação da partida</h2>

      <div className="progress" role="status" aria-live="polite">
        {job === null && polling.phase === "polling" ? (
          <p aria-busy="true">Consultando a importação…</p>
        ) : null}
        {job !== null ? (
          <>
            <p className={`progress__state progress__state--${job.status.toLowerCase()}`}>
              {STATE_LABEL[job.status]}
            </p>
            {job.attempts > 0 && !isTerminal(job) ? (
              <p className="muted">Tentativa {job.attempts}</p>
            ) : null}
            {job.status === "RETRY_WAIT" ? (
              <p className="muted">
                {retryAtText ? `Próxima tentativa por volta de ${retryAtText}. ` : ""}
                Você pode sair desta tela: a importação continua e a partida aparece na lista.
              </p>
            ) : null}
          </>
        ) : null}
      </div>

      {job?.status === "RETRY_WAIT" && job.errorCode ? (
        <ErrorNotice code={job.errorCode} title="Última tentativa falhou (ainda vai tentar de novo)" />
      ) : null}

      {job?.status === "FAILED" ? (
        <>
          <ErrorNotice code={job.errorCode} title="Não foi possível importar essa partida" />
          <button
            type="button"
            className="button"
            onClick={() => void handleRetry()}
            disabled={retrying}
          >
            {retrying ? "Reenviando…" : "Tentar importar novamente"}
          </button>
        </>
      ) : null}

      {retryError ? <ErrorNotice error={retryError} /> : null}

      {polling.phase === "failed" ? (
        <ErrorNotice
          error={polling.error}
          onRetry={notFound ? undefined : polling.restart}
          retryLabel="Consultar novamente"
        />
      ) : null}

      {polling.phase === "stalled" ? (
        <div className="notice notice--info">
          <p>O acompanhamento automático parou depois de muito tempo. A importação segue no servidor.</p>
          <button type="button" className="button button--secondary" onClick={polling.restart}>
            Continuar acompanhando
          </button>
        </div>
      ) : null}

      <p>
        <Link to="/">Voltar ao início</Link>
      </p>
    </section>
  );
}
