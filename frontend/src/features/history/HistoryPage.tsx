import { useState } from "react";
import { api, type MatchSummary } from "../../shared/api/client";
import { usePagedList } from "../../shared/hooks/usePagedList";
import { Link } from "../../shared/router/Link";
import { isDemoMatchId } from "../../shared/router/routes";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { PREPARATION_LABEL } from "../matches/format";
import { filterLoadedMatches, type OriginFilter } from "./filter";

const PAGE_SIZE = 20;

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? "Data não disponível"
    : date.toLocaleString("pt-BR", { dateStyle: "medium", timeStyle: "short" });
}

function formatDuration(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function MatchRow({ match }: { match: MatchSummary }) {
  const demo = isDemoMatchId(match.matchId);
  const names = [...new Set((match.championsPresent ?? []).map((champion) =>
    champion.name ?? `Campeão #${champion.championId}`,
  ))];
  return (
    <li className="history-row">
      <div className="history-row__lead">
        <span className={`origin-badge${demo ? " origin-badge--demo" : ""}`}>
          {demo ? "DEMO · sintética" : "RIOT · importada"}
        </span>
        <strong className="history-row__id">{match.matchId}</strong>
        <span className="history-row__date">{formatDate(match.startAt)}</span>
      </div>
      <div className="history-row__details">
        <span>Patch {match.patch} · duração {formatDuration(match.durationSeconds)}</span>
        <span>Preparação: {PREPARATION_LABEL[match.preparationStatus]}</span>
        <span>
          Campeões presentes: {names.length ? names.join(", ") : "não disponíveis"}
        </span>
      </div>
      <Link
        to={{ name: "historyMatch", matchId: match.matchId, participantId: null }}
        className="history-row__action"
        aria-label={`Contexto da partida ${match.matchId}`}
      >
        Contexto <span aria-hidden="true">↗</span>
      </Link>
    </li>
  );
}

export function HistoryPage() {
  const [query, setQuery] = useState("");
  const [origin, setOrigin] = useState<OriginFilter>("ALL");
  const list = usePagedList<MatchSummary>({
    resetKey: "history-matches",
    fetchPage: (cursor, signal) => api.listMatches({ cursor, limit: PAGE_SIZE }, signal),
  });
  const visible = filterLoadedMatches(list.items, query, origin);
  const demoCount = list.items.filter((match) => isDemoMatchId(match.matchId)).length;
  const realCount = list.items.length - demoCount;

  return (
    <div className="history-page">
      <div className="page-intro">
        <p className="page-intro__kicker">Histórico de partidas importadas</p>
        <h2>Partidas para estudar</h2>
        <p>Abra uma partida concluída, escolha um participante e examine as decisões de compra.</p>
      </div>

      <section className="history-summary" aria-label="Resumo das páginas carregadas">
        <strong>{list.items.length} partidas carregadas</strong>
        <span>{realCount} RIOT · {demoCount} DEMO</span>
        <p>O resumo cobre somente as páginas carregadas. Não representa todas as partidas do servidor nem partidas da sua conta.</p>
      </section>

      <section aria-label="Busca e filtros do histórico" className="history-controls">
        <label htmlFor="history-query">Buscar por identificador ou campeão presente</label>
        <input
          id="history-query"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="BR1_… ou nome de campeão"
          aria-describedby="history-search-scope"
        />
        <p id="history-search-scope" className="muted">
          Busca nas páginas carregadas. O campeão pode ser de qualquer participante da partida.
        </p>
        <div className="history-filter-group" role="group" aria-label="Origem da partida">
          {(["ALL", "RIOT", "DEMO"] as const).map((value) => (
            <button
              key={value}
              type="button"
              className={origin === value ? "filter-button filter-button--active" : "filter-button"}
              aria-pressed={origin === value}
              onClick={() => setOrigin(value)}
            >
              {value === "ALL" ? "Todas as origens" : value === "RIOT" ? "RIOT" : "DEMO"}
            </button>
          ))}
        </div>
        <div className="history-filter-group" role="group" aria-label="Resultado da partida">
          <span className="filter-button filter-button--active">Todos os resultados</span>
          <button type="button" className="filter-button" disabled>Vitórias</button>
          <button type="button" className="filter-button" disabled>Derrotas</button>
        </div>
        <p className="muted">Vitórias e derrotas ficam indisponíveis: a API não identifica qual participante é você.</p>
      </section>

      {list.status === "loading" ? <p aria-busy="true">Carregando partidas…</p> : null}
      {list.status === "error" ? (
        <ErrorNotice error={list.error} onRetry={list.reload} retryLabel="Tentar novamente" />
      ) : null}
      {list.status === "ready" ? (
        <section aria-labelledby="history-list-heading">
          <div className="history-list-heading">
            <h3 id="history-list-heading">Partidas</h3>
            <span>{visible.length} de {list.items.length} carregadas neste filtro</span>
          </div>
          {visible.length ? (
            <ul className="history-list">{visible.map((match) => <MatchRow key={match.matchId} match={match} />)}</ul>
          ) : (
            <div className="empty-state">
              <h4>{list.items.length ? "Nenhuma partida neste filtro" : "Nenhuma partida importada"}</h4>
              <p>{list.items.length ? "Ajuste a busca ou carregue mais páginas." : "Importe uma partida concluída ou abra a demonstração para começar."}</p>
              <Link to="/">Importar ou abrir demonstração</Link>
            </div>
          )}
          {list.loadMoreError ? <ErrorNotice error={list.loadMoreError} onRetry={list.loadMore} retryLabel="Tentar carregar mais" /> : null}
          {list.nextCursor !== null ? (
            <button className="button button--secondary" type="button" onClick={list.loadMore} disabled={list.loadingMore}>
              {list.loadingMore ? "Carregando…" : "Carregar mais partidas"}
            </button>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
