import { api, reportUrl, type LearningAttemptSummary } from "../../shared/api/client";
import { usePagedList } from "../../shared/hooks/usePagedList";
import { Link } from "../../shared/router/Link";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { shortWhen } from "../history/presentation";
import "./study.css";

const PAGE_SIZE = 20;

const STATUS_LABEL: Record<LearningAttemptSummary["status"], string> = {
  OPEN: "Em aberto",
  ANSWERED: "Respondida",
  REVEALED: "Revelada",
};

const STATUS_CHIP: Record<LearningAttemptSummary["status"], string> = {
  OPEN: "chip",
  ANSWERED: "chip chip--accent",
  REVEALED: "chip chip--win",
};

const MODE_LABEL: Record<LearningAttemptSummary["mode"], string> = {
  STUDY: "Modo estudo",
  TRANSFER: "Transferência (sem pistas)",
};

function AttemptRow({ attempt }: { attempt: LearningAttemptSummary }) {
  return (
    <li className="attempt-row">
      <span className={STATUS_CHIP[attempt.status]}>{STATUS_LABEL[attempt.status]}</span>
      <span className="attempt-row__main">
        <strong>Partida {attempt.matchId}</strong>
        <span className="attempt-row__meta">
          {MODE_LABEL[attempt.mode]} · {shortWhen(attempt.createdAt)}
          {attempt.hasReflection ? " · com reflexão" : ""}
        </span>
      </span>
      <span className="attempt-row__actions">
        <Link to={{ name: "decision", decisionId: attempt.decisionId }} className="arrow-link">
          Abrir decisão
        </Link>
        {attempt.analysisId ? (
          <a
            className="button button--secondary"
            href={reportUrl(attempt.analysisId, "html", attempt.attemptId)}
            download
          >
            Ver relatório
          </a>
        ) : null}
      </span>
    </li>
  );
}

/** Biblioteca de decisões: tentativas do modo estudo de qualquer partida, não só da que está
 * sendo revisada agora — para estudar fora do contexto de uma partida específica. */
export function DecisionLibraryPage() {
  const list = usePagedList<LearningAttemptSummary>({
    resetKey: "decision-library",
    fetchPage: (cursor, signal) => api.listAttempts({ cursor, limit: PAGE_SIZE }, signal),
  });

  return (
    <div className="area-page study-page">
      <header className="page-head">
        <p className="page-head__context">Modo estudo</p>
        <h2>Biblioteca de decisões</h2>
        <p className="page-head__lead">
          Suas tentativas de qualquer partida, mais recentes primeiro — para estudar fora do
          contexto de uma revisão específica.
        </p>
      </header>

      {list.status === "loading" ? (
        <div className="study-state" role="status" aria-busy="true">Carregando…</div>
      ) : null}
      {list.status === "error" ? (
        <div className="study-state"><ErrorNotice error={list.error} onRetry={list.reload} retryLabel="Tentar novamente" /></div>
      ) : null}
      {list.status === "ready" ? (
        list.items.length ? (
          <section aria-label="Tentativas registradas">
            <ul className="surface row-list attempt-list">
              {list.items.map((attempt) => <AttemptRow key={attempt.attemptId} attempt={attempt} />)}
            </ul>
            {list.loadMoreError ? (
              <ErrorNotice error={list.loadMoreError} onRetry={list.loadMore} retryLabel="Tentar carregar mais" />
            ) : null}
            {list.nextCursor !== null ? (
              <button
                className="button button--secondary study-more"
                type="button"
                onClick={list.loadMore}
                disabled={list.loadingMore}
              >
                {list.loadingMore ? "Carregando…" : "Carregar mais tentativas"}
              </button>
            ) : null}
          </section>
        ) : (
          <div className="empty-state">
            <h4>Nenhuma tentativa ainda</h4>
            <p>Decisões que você responder no modo estudo aparecem aqui.</p>
          </div>
        )
      ) : null}
    </div>
  );
}
