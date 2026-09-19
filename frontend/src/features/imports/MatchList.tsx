import { api, type MatchSummary } from "../../shared/api/client";
import { usePagedList } from "../../shared/hooks/usePagedList";
import { Link } from "../../shared/router/Link";
import { isDemoMatchId } from "../../shared/router/routes";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { PREPARATION_LABEL } from "../matches/format";

const PAGE_SIZE = 20;

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? "data desconhecida"
    : date.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

function MatchRow({ match }: { match: MatchSummary }) {
  return (
    <li className="list__item">
      <Link to={{ name: "match", matchId: match.matchId, participantId: null }} className="list__link">
        <span className="list__title">{match.matchId}</span>
        {isDemoMatchId(match.matchId) ? <span className="tag">Demonstração</span> : null}
      </Link>
      <span className={`badge badge--${match.preparationStatus.toLowerCase()}`}>
        {PREPARATION_LABEL[match.preparationStatus]}
      </span>
      <span className="list__meta">
        Patch {match.patch} · fila {match.queue} · {formatDate(match.startAt)}
      </span>
    </li>
  );
}

/** "Partidas importadas": retomar uma partida já importada, com o estado da preparação. */
export function MatchList() {
  const list = usePagedList<MatchSummary>({
    resetKey: "matches",
    fetchPage: (cursor, signal) => api.listMatches({ cursor, limit: PAGE_SIZE }, signal),
  });

  return (
    <section aria-labelledby="matches-heading" className="section">
      <h2 id="matches-heading">Partidas importadas</h2>
      {list.status === "loading" ? (
        <p className="muted" aria-busy="true">
          Carregando partidas…
        </p>
      ) : null}
      {list.status === "error" ? (
        <ErrorNotice error={list.error} onRetry={list.reload} retryLabel="Recarregar lista" />
      ) : null}
      {list.status === "ready" && list.items.length === 0 ? (
        <p className="muted">
          Nenhuma partida importada ainda. Importe uma partida acima ou explore a demonstração.
        </p>
      ) : null}
      {list.items.length > 0 ? (
        <ul className="list">
          {list.items.map((match) => (
            <MatchRow key={match.matchId} match={match} />
          ))}
        </ul>
      ) : null}
      {list.loadMoreError ? <ErrorNotice error={list.loadMoreError} /> : null}
      {list.nextCursor !== null ? (
        <button
          type="button"
          className="button button--secondary"
          onClick={list.loadMore}
          disabled={list.loadingMore}
        >
          {list.loadingMore ? "Carregando…" : "Carregar mais partidas"}
        </button>
      ) : null}
      {list.status === "ready" ? (
        <button type="button" className="button button--link" onClick={list.reload}>
          Atualizar lista
        </button>
      ) : null}
    </section>
  );
}
