import { api, type DecisionSummary } from "../../shared/api/client";
import { isApiError } from "../../shared/api/errors";
import { usePagedList } from "../../shared/hooks/usePagedList";
import { Link } from "../../shared/router/Link";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { describeBudgetShort, formatGameTime, ordinalLabel, reasonText } from "./format";

const PAGE_SIZE = 20;

function DecisionRow({ decision }: { decision: DecisionSummary }) {
  const budget = describeBudgetShort(
    decision.budgetSource,
    decision.frameAgeMs,
    decision.staleFrame,
  );
  const ordinal = ordinalLabel(decision.mainItemOrdinal);
  const reasons = decision.reasonCodes ?? [];
  return (
    <li className="list__item">
      <Link to={{ name: "decision", decisionId: decision.decisionId }} className="list__link">
        <span className="list__title">Decisão em {formatGameTime(decision.timestampMs)}</span>
      </Link>
      <span className={`badge ${decision.eligible ? "badge--eligible" : "badge--ineligible"}`}>
        {decision.eligible ? "Elegível" : "Inelegível"}
      </span>
      {ordinal ? <span className="list__meta">{ordinal}</span> : null}
      <span className={`list__meta budget budget--${budget.tone}`}>{budget.text}</span>
      {reasons.length > 0 ? (
        <ul className="reasons">
          {reasons.map((code) => (
            <li key={code}>{reasonText(code)}</li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** Decisões de um participante (GET .../decisions), paginadas com "carregar mais". */
export function DecisionList({
  matchId,
  participantId,
}: {
  matchId: string;
  participantId: number;
}) {
  const list = usePagedList<DecisionSummary>({
    resetKey: `${matchId}:${participantId}`,
    fetchPage: (cursor, signal) =>
      api.listDecisions(matchId, participantId, { cursor, limit: PAGE_SIZE }, signal),
  });
  const cursorExpired = isApiError(list.loadMoreError) && list.loadMoreError.code === "INVALID_CURSOR";

  return (
    <section aria-labelledby="decisions-heading" className="section">
      <h3 id="decisions-heading">Decisões do participante {participantId}</h3>
      {list.status === "loading" ? (
        <p className="muted" aria-busy="true">
          Carregando decisões…
        </p>
      ) : null}
      {list.status === "error" ? (
        <ErrorNotice error={list.error} onRetry={list.reload} retryLabel="Recarregar decisões" />
      ) : null}
      {list.status === "ready" && list.items.length === 0 ? (
        <p className="muted">
          Nenhuma decisão de compra foi encontrada para este participante nesta partida.
        </p>
      ) : null}
      {list.items.length > 0 ? (
        <ol className="list">
          {list.items.map((decision) => (
            <DecisionRow key={decision.decisionId} decision={decision} />
          ))}
        </ol>
      ) : null}
      {list.loadMoreError ? (
        <ErrorNotice
          error={list.loadMoreError}
          onRetry={cursorExpired ? list.reload : list.loadMore}
          retryLabel={cursorExpired ? "Recarregar decisões" : "Tentar carregar mais"}
        />
      ) : null}
      {list.nextCursor !== null && !list.loadMoreError ? (
        <button
          type="button"
          className="button button--secondary"
          onClick={list.loadMore}
          disabled={list.loadingMore}
        >
          {list.loadingMore ? "Carregando…" : "Carregar mais decisões"}
        </button>
      ) : null}
    </section>
  );
}
