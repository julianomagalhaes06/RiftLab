import { useRef, useState, type RefObject } from "react";
import { api, type MatchSummary } from "../../shared/api/client";
import { usePagedList } from "../../shared/hooks/usePagedList";
import { Link } from "../../shared/router/Link";
import { isDemoMatchId } from "../../shared/router/routes";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { PREPARATION_LABEL } from "../matches/format";
import { filterLoadedMatches, type OriginFilter } from "./filter";
import { matchDate, matchDuration, matchOrigin, presentChampions } from "./presentation";

const PAGE_SIZE = 20;

function MatchRow({ match, selected, onSelect }: { match: MatchSummary; selected: boolean; onSelect(): void }) {
  const names = presentChampions(match);
  return (
    <li className={`history-row${selected ? " history-row--selected" : ""}`}>
      <div className="history-row__identity">
        <span className={`origin-badge${isDemoMatchId(match.matchId) ? " origin-badge--demo" : ""}`}>{matchOrigin(match)}</span>
        <strong className="history-row__id">{match.matchId}</strong>
        <span className="history-row__date">{matchDate(match.startAt)}</span>
      </div>
      <div className="history-row__details">
        <span>Duração {matchDuration(match.durationSeconds)} · Patch {match.patch}</span>
        <span>Campeões presentes: {names.length ? names.join(", ") : "não disponíveis"}</span>
      </div>
      <div className="history-row__actions">
        <button type="button" className="history-row__select" onClick={onSelect} aria-pressed={selected} aria-controls="history-selection" aria-label={`Selecionar detalhes da partida ${match.matchId}`}>
          {selected ? "Selecionada" : "Detalhes"}
        </button>
        <Link to={{ name: "historyMatch", matchId: match.matchId, participantId: null }} className="history-row__action" aria-label={`Contexto da partida ${match.matchId}`}>
          Contexto <span aria-hidden="true">↗</span>
        </Link>
      </div>
    </li>
  );
}

function MatchSelection({ match, panelRef }: { match: MatchSummary | null; panelRef: RefObject<HTMLElement | null> }) {
  if (!match) return (
    <aside className="history-selection" id="history-selection" tabIndex={-1} ref={panelRef} aria-label="Detalhes da partida selecionada">
      <h3>Detalhes da partida</h3>
      <p>Selecione uma partida nas páginas carregadas para examinar seus metadados.</p>
    </aside>
  );
  const names = presentChampions(match);
  return (
    <aside className="history-selection" id="history-selection" tabIndex={-1} ref={panelRef} aria-labelledby="history-selection-heading">
      <p className="history-selection__eyebrow">Seleção das páginas carregadas</p>
      <h3 id="history-selection-heading">Detalhes da partida</h3>
      <span className={`origin-badge${isDemoMatchId(match.matchId) ? " origin-badge--demo" : ""}`}>{matchOrigin(match)}</span>
      <strong className="history-selection__id">{match.matchId}</strong>
      <dl className="history-selection__facts">
        <div><dt>Início</dt><dd>{matchDate(match.startAt)}</dd></div>
        <div><dt>Duração</dt><dd>{matchDuration(match.durationSeconds)}</dd></div>
        <div><dt>Patch</dt><dd>{match.patch}</dd></div>
        <div><dt>Preparação</dt><dd>{PREPARATION_LABEL[match.preparationStatus]}</dd></div>
      </dl>
      <div className="history-selection__champions">
        <h4>Campeões presentes</h4>
        <p>{names.length ? names.join(", ") : "Nomes não disponíveis no catálogo desta partida."}</p>
        <small>Podem pertencer a qualquer participante; não identificam a conta própria.</small>
      </div>
      <Link to={{ name: "historyMatch", matchId: match.matchId, participantId: null }} className="button">Abrir Contexto</Link>
    </aside>
  );
}

export function HistoryPage() {
  const [query, setQuery] = useState("");
  const [origin, setOrigin] = useState<OriginFilter>("ALL");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selectionRef = useRef<HTMLElement>(null);
  const list = usePagedList<MatchSummary>({
    resetKey: "history-matches",
    fetchPage: (cursor, signal) => api.listMatches({ cursor, limit: PAGE_SIZE }, signal),
  });
  const visible = filterLoadedMatches(list.items, query, origin);
  const selected = visible.find((match) => match.matchId === selectedId) ?? visible[0] ?? null;
  const demoCount = list.items.filter((match) => isDemoMatchId(match.matchId)).length;
  const realCount = list.items.length - demoCount;

  function selectMatch(matchId: string) {
    setSelectedId(matchId);
    if (window.innerWidth <= 900) {
      requestAnimationFrame(() => {
        selectionRef.current?.focus();
        selectionRef.current?.scrollIntoView({ block: "start", behavior: "auto" });
      });
    }
  }

  return (
    <div className="history-page">
      <div className="page-intro">
        <p className="page-intro__kicker">Histórico de partidas importadas</p>
        <h2>Partidas para estudar</h2>
        <p>Filtre os registros carregados e abra o contexto de uma partida concluída.</p>
      </div>

      <section className="history-summary" aria-label="Resumo das páginas carregadas">
        <strong>{list.items.length} partidas carregadas</strong>
        <span>{realCount} RIOT · {demoCount} DEMO</span>
        <p>Este recorte cobre só as páginas carregadas; não representa todas as partidas do servidor nem as partidas da sua conta.</p>
      </section>

      <section aria-label="Busca e filtros do histórico" className="history-controls">
        <div className="history-search">
          <label htmlFor="history-query">Buscar por identificador ou campeão presente</label>
          <input id="history-query" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="BR1_… ou nome de campeão" aria-describedby="history-search-scope" />
          <p id="history-search-scope">Busca nas páginas carregadas. O campeão pode ser de qualquer participante.</p>
        </div>
        <div className="history-filter-group" role="group" aria-label="Origem da partida">
          {(["ALL", "RIOT", "DEMO"] as const).map((value) => (
            <button key={value} type="button" className={origin === value ? "filter-button filter-button--active" : "filter-button"} aria-pressed={origin === value} onClick={() => setOrigin(value)}>
              {value === "ALL" ? "Todas as origens" : value}
            </button>
          ))}
        </div>
        <p className="history-controls__limit">Filtro de resultado indisponível: a API não informa qual participante é a conta própria.</p>
      </section>

      {list.status === "loading" && <div className="history-state" role="status" aria-busy="true">Carregando partidas…</div>}
      {list.status === "error" && <div className="history-state"><ErrorNotice error={list.error} onRetry={list.reload} retryLabel="Tentar novamente" /></div>}
      {list.status === "ready" && (
        <section aria-labelledby="history-list-heading">
          <div className="history-list-heading">
            <h3 id="history-list-heading">Partidas importadas</h3>
            <span>{visible.length} de {list.items.length} carregadas neste filtro</span>
          </div>
          {visible.length ? (
            <div className="history-content">
              <div className="history-content__list">
                <ul className="history-list" aria-label="Partidas carregadas">{visible.map((match) => <MatchRow key={match.matchId} match={match} selected={selected?.matchId === match.matchId} onSelect={() => selectMatch(match.matchId)} />)}</ul>
                {list.loadMoreError ? <ErrorNotice error={list.loadMoreError} onRetry={list.loadMore} retryLabel="Tentar carregar mais" /> : null}
                {list.nextCursor !== null && <button className="button button--secondary" type="button" onClick={list.loadMore} disabled={list.loadingMore}>{list.loadingMore ? "Carregando…" : "Carregar mais partidas"}</button>}
              </div>
              <MatchSelection match={selected} panelRef={selectionRef} />
            </div>
          ) : (
            <div className="empty-state">
              <h4>{list.items.length ? "Nenhuma partida neste filtro" : "Nenhuma partida importada"}</h4>
              <p>{list.items.length ? "Ajuste a busca ou carregue mais páginas." : "Importe uma partida concluída ou abra a demonstração para começar."}</p>
              <Link to="/">Importar ou abrir demonstração</Link>
              {list.nextCursor !== null && <button className="button button--secondary" type="button" onClick={list.loadMore} disabled={list.loadingMore}>{list.loadingMore ? "Carregando…" : "Carregar mais partidas"}</button>}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
