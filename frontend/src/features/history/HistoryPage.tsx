import { useCallback, useEffect, useState } from "react";
import { api, type LearningProgress, type MatchSummary, type PlayerProfile } from "../../shared/api/client";
import { usePagedList } from "../../shared/hooks/usePagedList";
import { Link } from "../../shared/router/Link";
import { isDemoMatchId } from "../../shared/router/routes";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { championSplashUrl } from "../matches/format";
import { GameIcon } from "../../shared/ui/GameIcon";
import { betterRank, emblemSrc, isTier, profileIconUrl, rankTitle } from "../ranked/tiers";
import { filterLoadedMatches, type OriginFilter, type QueueFilter, type ResultFilter } from "./filter";
import { HistorySync, type SyncPending } from "./HistorySync";
import { SyncPendingBox } from "./SyncPendingBox";
import { DayGroups, FilterGroup, LoadedSummary } from "./HistoryParts";
import { loadedHighlights, summarizeLoaded, winStreak } from "./presentation";
import { SplashArt } from "../../shared/ui/SplashArt";
import "./history.css";
import { useDemoNotice } from "../../app/demoNotice";
import { useAuth } from "../auth/authContext";

const PAGE_SIZE = 20;

/** Progresso de estudo (tentativas, revelações e reflexões) com atalho para Minha evolução. */
function StudyProgress({ progress }: { progress: LearningProgress }) {
  const attempts = progress.study.attempts + progress.transfer.attempts;
  const revealed = progress.study.revealed + progress.transfer.revealed;
  const reflections = progress.study.reflections + progress.transfer.reflections;
  return (
    <div className="history-card history-card--study surface">
      <h3 className="history-card__title"><GameIcon name="book" size={16} /> Estudo</h3>
      {attempts ? (
        <>
          <p className="history-study__big"><strong>{attempts}</strong> {attempts === 1 ? "decisão estudada" : "decisões estudadas"}</p>
          <dl className="history-study__stats">
            <div><dt>Reveladas</dt><dd>{revealed}</dd></div>
            <div><dt>Reflexões</dt><dd>{reflections}</dd></div>
          </dl>
          <div className="history-study__bar" aria-hidden="true"><span style={{ width: `${Math.min(100, (reflections / attempts) * 100)}%` }} /></div>
        </>
      ) : <p className="history-study__empty">Nenhuma decisão estudada ainda. Abra uma partida e analise uma compra.</p>}
      <Link to={{ name: "myEvolution" }} className="arrow-link">Minha evolução</Link>
    </div>
  );
}

/** Banner do perfil, como no tracker.gg: splash do campeão mais jogado, ícone, Riot ID e elo. */
function HistoryBanner({ profile, splash, streak }: { profile: PlayerProfile | null; splash: string | null; streak: number }) {
  const [iconBroken, setIconBroken] = useState(false);
  const rank = profile ? betterRank(profile.solo, profile.flex) : null;
  const tier = rank?.state === "READY" && isTier(rank.tier) ? rank.tier : null;
  const icon = profile ? profileIconUrl(profile) : null;
  return (
    <header className="history-banner splash-banner">
      {splash ? <SplashArt key={splash} src={splash} /> : null}
      <div className="history-banner__inner">
        <div className="history-banner__identity">
          <span className="portrait history-banner__icon">
            {icon && !iconBroken ? <img src={icon} alt="" width="72" height="72" onError={() => setIconBroken(true)} /> : <GameIcon name="crosshair" size={30} />}
          </span>
          <div>
            <p className="history-banner__context">Histórico de partidas importadas</p>
            <h2>Partidas para estudar</h2>
            {profile ? (
              <p className="history-banner__who">
                <span>{profile.gameName}<span className="history-banner__tag">#{profile.tagLine}</span></span>
                {tier ? <span className="history-banner__rank" data-tier={tier}><img src={emblemSrc(tier)} alt="" width="20" height="20" />{rankTitle(tier, rank?.division)} · {rank?.leaguePoints ?? 0} PDL</span> : null}
              </p>
            ) : null}
            {streak >= 2 ? <p className="banner-streak"><GameIcon name="flame" size={16} /> <strong>{streak}</strong> vitórias seguidas</p> : null}
          </div>
        </div>
        <div className="history-banner__side">
          {/* CC-1202: a importação manual saiu do topo e fica junto das partidas. */}
          <Link to="/importar" className="arrow-link history-banner__import">Importar partida</Link>
        </div>
      </div>
    </header>
  );
}

export function HistoryPage() {
  const { user, demoEnabled } = useAuth();
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
  const [profile, setProfile] = useState<PlayerProfile | null>(null);
  const [progress, setProgress] = useState<LearningProgress | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api.getOwnPlayer(controller.signal).then((own) => setProfile(own.profile ?? null)).catch(() => undefined);
    api.getProgress(controller.signal).then(setProgress).catch(() => undefined);
    return () => controller.abort();
  }, []);
  const visible = filterLoadedMatches(list.items, query, origin, result, queue);
  const loadedSummary = summarizeLoaded(list.items);
  const highlights = loadedHighlights(list.items);
  const hasLocal = list.items.some((match) => match.localParticipantId != null);
  useDemoNotice(list.items.some((match) => isDemoMatchId(match.matchId)));

  return (
    <div className="history-page">
      <HistoryBanner profile={profile} streak={winStreak(loadedSummary.recent)}
        splash={championSplashUrl(loadedSummary.champions[0]?.participant.champion?.imageFile)} />

      <HistorySync onNewMatches={list.refresh} latestIds={latestIds} knownIds={() => list.items.map((m) => m.matchId)} onPendingChange={setPending} />

      <div className={list.status === "ready" && list.items.length > 0 ? "history-shell history-shell--aside" : "history-shell"}>
      {list.status === "ready" && list.items.length > 0 && <aside className="history-aside">
        <LoadedSummary matches={list.items} />
        {progress ? <StudyProgress progress={progress} /> : null}
      </aside>}
      <div className="history-main">
      <section aria-label="Busca e filtros do histórico" className="history-controls surface">
        <div className="history-search">
          <label htmlFor="history-query">Buscar campeão ou partida</label>
          <input id="history-query" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Nome do campeão ou ID da partida" aria-describedby="history-search-scope" />
        </div>
        <div className="history-filters">
          <FilterGroup<QueueFilter> label="Fila" value={queue} onChange={setQueue} options={[["ALL", "Todas as filas"], [420, "Solo/Duo"], [440, "Flex"]] as const} />
          {hasLocal ? (
            <FilterGroup<ResultFilter> label="Resultado" value={result} onChange={setResult} options={[["ALL", "Todos"], ["WIN", "Vitórias"], ["LOSS", "Derrotas"]] as const} />
          ) : null}
          {demoEnabled ? <FilterGroup<OriginFilter> label="Origem da partida" value={origin} onChange={setOrigin} options={[["ALL", "Minha conta e DEMO"], ["RIOT", "Minha conta"], ["DEMO", "DEMO"]] as const} /> : null}
        </div>
        <p id="history-search-scope" className="history-controls__hint">Nas páginas carregadas; vale o campeão de qualquer participante.</p>
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
              <DayGroups matches={visible} highlights={highlights} />
              {list.loadMoreError ? <ErrorNotice error={list.loadMoreError} onRetry={list.loadMore} retryLabel="Tentar carregar mais" /> : null}
              {list.nextCursor !== null && <button className="button button--secondary history-more" type="button" onClick={list.loadMore} disabled={list.loadingMore}>{list.loadingMore ? "Carregando…" : "Carregar mais partidas"}</button>}
            </>
          ) : pending && !list.items.length ? null : (
            <div className="empty-state">
              <h4>{list.items.length ? "Nenhuma partida neste filtro" : "Nenhuma partida importada"}</h4>
              <p>{list.items.length ? "Ajuste a busca ou carregue mais páginas." : origin === "RIOT" ? (user ? "Suas partidas ranqueadas aparecem aqui quando sua conta Riot estiver vinculada." : "Nenhuma partida foi identificada para a conta configurada. Confira a conta e a atualização em Minha posição.") : "Importe uma partida concluída para começar."}</p>
              {user && origin === "RIOT" && !list.items.length ? <Link to="/conta">Ir para Minha conta</Link> : null}
              {list.nextCursor !== null && <button className="button button--secondary" type="button" onClick={list.loadMore} disabled={list.loadingMore}>{list.loadingMore ? "Carregando…" : "Carregar mais partidas"}</button>}
            </div>
          )}
        </section>
      )}
      </div>
      </div>
    </div>
  );
}
