import { api, type MatchSummary } from "../../shared/api/client";
import { usePagedList } from "../../shared/hooks/usePagedList";
import { Link } from "../../shared/router/Link";
import { isDemoMatchId } from "../../shared/router/routes";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { matchDate, matchDuration, matchOrigin, presentChampions } from "./presentation";
import { useDemoNotice } from "../../app/demoNotice";

function RecentRow({ match }: { match: MatchSummary }) {
  const champions = presentChampions(match);
  return (
    <li className="recent-row">
      <div className="recent-row__identity">
        <span className={`origin-badge${isDemoMatchId(match.matchId) ? " origin-badge--demo" : ""}`}>
          {matchOrigin(match)}
        </span>
        <strong>{match.matchId}</strong>
        <span>Campeões presentes: {champions.length ? champions.join(", ") : "não disponíveis"}</span>
      </div>
      <div className="recent-row__time">
        <time dateTime={match.startAt}>{matchDate(match.startAt)}</time>
        <span>Duração {matchDuration(match.durationSeconds)}</span>
      </div>
      <Link
        to={{ name: "historyMatch", matchId: match.matchId, participantId: null }}
        className="recent-row__action"
        aria-label={`Contexto da partida ${match.matchId}`}
      >Contexto <span aria-hidden="true">↗</span></Link>
    </li>
  );
}

export function RecentMatches() {
  const list = usePagedList<MatchSummary>({
    resetKey: "panel-recent-imported",
    fetchPage: (_cursor, signal) => api.listMatches({ limit: 5 }, signal),
  });
  useDemoNotice(list.items.some((match) => isDemoMatchId(match.matchId)));
  return (
    <section className="recent-matches" aria-labelledby="recent-matches-heading">
      <div className="recent-matches__heading">
        <div>
          <h3 id="recent-matches-heading">Partidas importadas recentes</h3>
          <p>Registros disponíveis para estudo, de qualquer origem importada.</p>
        </div>
      </div>
      {list.status === "loading" && <div className="recent-matches__state" role="status" aria-busy="true">Carregando partidas recentes…</div>}
      {list.status === "error" && <div className="recent-matches__state"><ErrorNotice error={list.error} onRetry={list.reload} retryLabel="Tentar novamente" /></div>}
      {list.status === "ready" && (
        list.items.length ? <>
          <ul className="recent-matches__list">{list.items.map((match) => <RecentRow key={match.matchId} match={match} />)}</ul>
          <div className="recent-matches__footer">
            <span>Mostrando {list.items.length} {list.items.length === 1 ? "partida importada recente" : "partidas importadas recentes"}. Total no servidor não informado.</span>
            <Link to="/historico">Abrir Histórico</Link>
          </div>
        </> : <div className="recent-matches__state recent-matches__state--empty">
          <strong>Nenhuma partida importada</strong>
          <p>Importe uma partida concluída ou abra a demonstração para iniciar o estudo.</p>
          <Link to="/importar">Importar ou abrir demonstração</Link>
        </div>
      )}
    </section>
  );
}
