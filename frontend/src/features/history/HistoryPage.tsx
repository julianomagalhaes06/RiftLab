import { useCallback, useState } from "react";
import { api, type MatchSummary } from "../../shared/api/client";
import { usePagedList } from "../../shared/hooks/usePagedList";
import { Link } from "../../shared/router/Link";
import { isDemoMatchId } from "../../shared/router/routes";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { championName } from "../matches/format";
import { ChampionAvatar } from "./ChampionAvatar";
import { filterLoadedMatches, type OriginFilter, type QueueFilter, type ResultFilter } from "./filter";
import { HistorySync, type SyncPending } from "./HistorySync";
import { SyncPendingBox } from "./SyncPendingBox";
import { MatchCard } from "./MatchCard";
import { dayKey, dayLabel, decimal, percent, record, summarizeLoaded } from "./presentation";
import "./history.css";
import { count } from "../../shared/text/count";
import { useDemoNotice } from "../../app/demoNotice";

const PAGE_SIZE = 20;

function FilterGroup<T extends string | number>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: readonly (readonly [T, string])[];
  onChange(value: T): void;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map(([option, text]) => (
        <button key={String(option)} type="button" aria-pressed={value === option} onClick={() => onChange(option)}>
          {text}
        </button>
      ))}
    </div>
  );
}

function LoadedSummary({ matches }: { matches: MatchSummary[] }) {
  const summary = summarizeLoaded(matches);
  const demoCount = matches.filter((match) => isDemoMatchId(match.matchId)).length;
  if (!summary.games) {
    return (
      <section className="history-summary surface surface--pad" aria-label="Resumo das páginas carregadas">
        <strong>{count(matches.length, "partida carregada", "partidas carregadas")}</strong>
        <span>{matches.length - demoCount} RIOT · {demoCount} DEMO</span>
        <p>Sua conta não foi identificada nas partidas carregadas; resultado e KDA dependem da observação de elo da conta configurada.</p>
      </section>
    );
  }
  const rate = summary.wins + summary.losses ? summary.wins / (summary.wins + summary.losses) : null;
  // CC-1205: card de métricas do padrão (taxa de vitória e KDA lado a lado, campeões embaixo).
  return (
    <section className="history-overview surface" aria-label="Resumo das suas partidas carregadas">
      <div className="history-overview__row">
        <div className="metric">
          <p className="metric__label">Taxa de vitória</p>
          <p className="metric__value">{rate === null ? "—" : percent(rate)}</p>
          <p className="metric__foot">
            <span className="result-text--win">{summary.wins}V</span> <span className="result-text--loss">{summary.losses}D</span> nas suas últimas {count(summary.games, "partida carregada", "partidas carregadas")}
          </p>
          {rate !== null ? <div className="metric__bar" aria-hidden="true"><span style={{ width: `${rate * 100}%` }} /></div> : null}
        </div>
        <div className="metric">
          <p className="metric__label">KDA</p>
          <p className="metric__value">{summary.kda === null ? "—" : decimal(summary.kda, 2)}</p>
          {summary.averages ? (
            <p className="metric__foot">{decimal(summary.averages.kills)} / {decimal(summary.averages.deaths)} / {decimal(summary.averages.assists)} em média</p>
          ) : null}
        </div>
      </div>
      <div className="history-overview__secondary">
        <ul className="history-overview__champions" aria-label="Campeões mais jogados nas partidas carregadas">
          {summary.champions.map((champion) => (
            <li key={champion.participant.championId}>
              <ChampionAvatar participant={champion.participant} size={36} className="portrait" />
              <span>
                <strong>{championName(champion.participant)}</strong>
                <small>{champion.wins}V {champion.losses}D · {count(champion.games, "jogo", "jogos")}</small>
              </span>
            </li>
          ))}
        </ul>
        <p className="history-overview__scope">Calculado só nas páginas carregadas ({count(matches.length, "partida", "partidas")}, {demoCount} DEMO).</p>
      </div>
    </section>
  );
}

export function DayGroups({ matches }: { matches: MatchSummary[] }) {
  const now = new Date();
  const groups: { key: string; label: string; items: MatchSummary[] }[] = [];
  for (const match of matches) {
    const key = dayKey(match.startAt);
    const last = groups.at(-1);
    if (last?.key === key) last.items.push(match);
    else groups.push({ key, label: dayLabel(match.startAt, now), items: [match] });
  }
  return (
    <div className="history-days">
      {groups.map((group) => {
        const day = record(group.items);
        return (
          <section key={group.key} className="history-day" aria-label={group.label}>
            <h4 className="history-day__heading">
              <span>{group.label}</span>
              {day.games ? (
                <small>
                  {day.wins}V {day.losses}D · {percent(day.wins / day.games)}
                </small>
              ) : null}
            </h4>
            <ul className="history-list" aria-label={`Partidas de ${group.label}`}>
              {group.items.map((match) => <MatchCard key={match.matchId} match={match} now={now} />)}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

export function HistoryPage() {
  const [query, setQuery] = useState("");
  const [origin, setOrigin] = useState<OriginFilter>("RIOT");
  const [result, setResult] = useState<ResultFilter>("ALL");
  const [queue, setQueue] = useState<QueueFilter>("ALL");
  const [pending, setPending] = useState<SyncPending | null>(null);
  // Partidas da conta (a sincronização só traz partidas da conta): para saber quando chegaram.
  const latestIds = useCallback(
    (signal?: AbortSignal) => api.listMatches({ limit: PAGE_SIZE, scope: "account" }, signal).then((page) => page.items.map((m) => m.matchId)),
    [],
  );
  const list = usePagedList<MatchSummary>({
    resetKey: `history-matches-${origin}`,
    fetchPage: (cursor, signal) => api.listMatches({
      cursor,
      limit: PAGE_SIZE,
      scope: origin === "RIOT" ? "account" : origin === "DEMO" ? "demo" : "account_and_demo",
    }, signal),
  });
  const visible = filterLoadedMatches(list.items, query, origin, result, queue);
  const hasLocal = list.items.some((match) => match.localParticipantId != null);
  useDemoNotice(list.items.some((match) => isDemoMatchId(match.matchId)));

  return (
    <div className="history-page">
      <header className="page-head history-head">
        <div>
          <p className="page-head__context">Histórico de partidas importadas</p>
          <h2>Partidas para estudar</h2>
          <p className="page-head__lead">Partidas da sua conta. Veja como foi cada partida e abra a análise das suas compras.</p>
        </div>
        {/* CC-1202: a importação manual saiu do topo e fica junto das partidas. */}
        <Link to="/importar" className="arrow-link">Importar partida</Link>
      </header>

      <HistorySync onNewMatches={list.refresh} latestIds={latestIds} knownIds={() => list.items.map((m) => m.matchId)} onPendingChange={setPending} />

      {list.status === "ready" && list.items.length > 0 && <LoadedSummary matches={list.items} />}

      <section aria-label="Busca e filtros do histórico" className="history-controls">
        <div className="history-search">
          <label htmlFor="history-query">Buscar campeão ou partida</label>
          <input id="history-query" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Nome do campeão ou ID da partida" aria-describedby="history-search-scope" />
          <p id="history-search-scope">Busca nas páginas carregadas. O campeão pode ser de qualquer participante.</p>
        </div>
        <div className="history-filters">
          <FilterGroup<QueueFilter> label="Fila" value={queue} onChange={setQueue} options={[["ALL", "Todas as filas"], [420, "Solo/Duo"], [440, "Flex"]] as const} />
          {hasLocal ? (
            <FilterGroup<ResultFilter> label="Resultado" value={result} onChange={setResult} options={[["ALL", "Todos"], ["WIN", "Vitórias"], ["LOSS", "Derrotas"]] as const} />
          ) : null}
          <FilterGroup<OriginFilter> label="Origem da partida" value={origin} onChange={setOrigin} options={[["ALL", "Minha conta e DEMO"], ["RIOT", "Minha conta"], ["DEMO", "DEMO"]] as const} />
        </div>
        {!hasLocal && list.status === "ready" ? (
          <p className="history-controls__limit">Filtro de resultado indisponível: sua conta não foi identificada nas partidas carregadas.</p>
        ) : null}
      </section>

      {pending ? <SyncPendingBox pending={pending} /> : null}
      {list.status === "loading" && !pending && <div className="history-state" role="status" aria-busy="true">Carregando partidas…</div>}
      {list.status === "error" && <div className="history-state"><ErrorNotice error={list.error} onRetry={list.reload} retryLabel="Tentar novamente" /></div>}
      {list.status === "ready" && (
        <section aria-labelledby="history-list-heading" className="history-results">
          <div className="history-list-heading">
            <h3 id="history-list-heading">Partidas</h3>
            <span>{visible.length} de {list.items.length} carregadas neste filtro</span>
          </div>
          {visible.length ? (
            <>
              <DayGroups matches={visible} />
              {list.loadMoreError ? <ErrorNotice error={list.loadMoreError} onRetry={list.loadMore} retryLabel="Tentar carregar mais" /> : null}
              {list.nextCursor !== null && <button className="button button--secondary history-more" type="button" onClick={list.loadMore} disabled={list.loadingMore}>{list.loadingMore ? "Carregando…" : "Carregar mais partidas"}</button>}
            </>
          ) : pending && !list.items.length ? null : (
            <div className="empty-state">
              <h4>{list.items.length ? "Nenhuma partida neste filtro" : "Nenhuma partida importada"}</h4>
              <p>{list.items.length ? "Ajuste a busca ou carregue mais páginas." : origin === "RIOT" ? "Nenhuma partida foi identificada para a conta configurada. Confira a conta e a atualização em Minha posição ou selecione DEMO." : "Importe uma partida concluída ou abra a demonstração para começar."}</p>
              <Link to="/importar">Importar ou abrir demonstração</Link>
              {list.nextCursor !== null && <button className="button button--secondary" type="button" onClick={list.loadMore} disabled={list.loadingMore}>{list.loadingMore ? "Carregando…" : "Carregar mais partidas"}</button>}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
