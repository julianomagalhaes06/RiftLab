import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type MatchSummary, type PlayerProfile, type PlayerRank } from "../../shared/api/client";
import { isAbortError, isApiError } from "../../shared/api/errors";
import { usePagedList } from "../../shared/hooks/usePagedList";
import { count } from "../../shared/text/count";
import { DayGroups, FilterGroup, LoadedSummary } from "../history/HistoryParts";
import { filterLoadedMatches, type QueueFilter, type ResultFilter } from "../history/filter";
import { loadedHighlights, summarizeLoaded, winStreak } from "../history/presentation";
import { championSplashUrl } from "../matches/format";
import { GameIcon } from "../../shared/ui/GameIcon";
import { SplashArt } from "../../shared/ui/SplashArt";
import { HistorySync, type SyncPending } from "../history/HistorySync";
import { SyncPendingBox } from "../history/SyncPendingBox";
import { clock } from "../history/syncMessage";
import { RankCard } from "../ranked/RankCard";
import { isTier, profileIconUrl } from "../ranked/tiers";
import { useRouter } from "../../shared/router/context";
import { useRiotIntent } from "../../shared/router/intent";
import { routeToPath } from "../../shared/router/routes";
import { LiveButton } from "../live/LiveButton";
import { LOOKUP_ERRORS } from "./lookupErrors";
import { rememberPlayer } from "./recentPlayers";
import { RiotConfirm } from "./RiotConfirm";
import "../history/history.css";
import "./players.css";

const PAGE_SIZE = 20;

/** Card de elo de uma fila do jogador (CC-1204): o mesmo do Painel e de Minha posição, sem
 * variação (a página do jogador guarda só a última observação). */
function QueueRank({ label, rank }: { label: string; rank: PlayerRank | null | undefined }) {
  const tier = rank?.state === "READY" && isTier(rank.tier) ? rank.tier : null;
  const games = (rank?.wins ?? 0) + (rank?.losses ?? 0);
  const record = tier && games
    ? `${count(rank?.wins ?? 0, "vitória", "vitórias")} · ${count(rank?.losses ?? 0, "derrota", "derrotas")} · ${Math.round(((rank?.wins ?? 0) / games) * 100)}%`
    : undefined;
  return (
    <RankCard
      heading={<h3>{label}</h3>}
      rank={tier && rank ? { tier, division: rank.division ?? null, leaguePoints: rank.leaguePoints ?? 0 } : null}
      caption={record}
    >
      {!rank ? <p className="rank-card__state">Sem observação</p> : null}
      {rank && !tier ? <p className="rank-card__state">Sem classificação</p> : null}
    </RankCard>
  );
}

/** Arte e sequência do banner: campeão mais jogado e vitórias seguidas nas partidas carregadas. */
interface BannerFacts { splash: string | null; streak: number }

function bannerFacts(matches: MatchSummary[]): BannerFacts {
  const summary = summarizeLoaded(matches);
  return { splash: championSplashUrl(summary.champions[0]?.participant.champion?.imageFile), streak: winStreak(summary.recent) };
}

function ProfileHeader({ profile, banner }: { profile: PlayerProfile; banner: BannerFacts }) {
  const [broken, setBroken] = useState(false);
  const src = profileIconUrl(profile);
  return (
    <>
      {/* Banner como no tracker.gg: splash do campeão que este jogador mais jogou nas partidas
          carregadas (a lista marca o jogador pesquisado como participante local). */}
      <div className="player-header splash-banner">
        {banner.splash ? <SplashArt key={banner.splash} src={banner.splash} /> : null}
        <span className="player-header__icon">
          <span className="portrait">
            {src && !broken ? (
              <img src={src} alt="" width="88" height="88" onError={() => setBroken(true)} />
            ) : (
              <span className="player-header__initial" aria-hidden="true">{profile.gameName.slice(0, 1).toUpperCase()}</span>
            )}
          </span>
          {profile.summonerLevel != null ? <span className="player-header__level" aria-hidden="true">{profile.summonerLevel}</span> : null}
        </span>
        <div className="player-header__who">
          <h2 id="player-heading">
            {profile.gameName} <span className="player-header__tag">#{profile.tagLine}</span>
          </h2>
          <p className="player-header__meta">
            {profile.summonerLevel != null ? `Nível ${profile.summonerLevel} · ` : ""}
            Atualizado às {clock(profile.refreshedAt) ?? "–"}
            {profile.staleReason === "RIOT_KEY_MISSING" ? " (sem chave da Riot para atualizar)" : ""}
          </p>
          {banner.streak >= 2 ? <p className="player-header__streak"><span className="hud-tag hud-tag--hot"><GameIcon name="flame" size={14} /> {banner.streak} vitórias seguidas</span></p> : null}
        </div>
        {/* CC-1211/CC-1213: partida ao vivo, com o aviso "jogando agora" como no op.gg. */}
        <LiveButton gameName={profile.gameName} tagLine={profile.tagLine} />
      </div>
      <div className="player-ranks" role="group" aria-label="Elo">
        <QueueRank label="Solo/Duo" rank={profile.solo} />
        <QueueRank label="Flex" rank={profile.flex} />
      </div>
    </>
  );
}

function PlayerMatches({ profile, onRefresh, onBanner }: { profile: PlayerProfile; onRefresh: (next: PlayerProfile) => void; onBanner: (facts: BannerFacts) => void }) {
  const [pending, setPending] = useState<SyncPending | null>(null);
  const latestIds = useCallback(
    (signal?: AbortSignal) => api.listMatches({ limit: PAGE_SIZE, player: profile.id }, signal).then((page) => page.items.map((m) => m.matchId)),
    [profile.id],
  );
  const list = usePagedList<MatchSummary>({
    resetKey: `player-${profile.id}`,
    fetchPage: (cursor, signal) => api.listMatches({ cursor, limit: PAGE_SIZE, player: profile.id }, signal),
  });
  const { splash, streak } = bannerFacts(list.items);
  useEffect(() => onBanner({ splash, streak }), [onBanner, splash, streak]);
  const calls = useMemo(
    () => ({
      // Atualizar: elo e ícone de novo, depois as partidas recentes (o worker importa as novas).
      request: async (signal?: AbortSignal, manual = false) => {
        if (manual) onRefresh(await api.lookupPlayer(profile.gameName, profile.tagLine, true));
        return api.requestPlayerSync(profile.id, profile.gameName, profile.tagLine, signal);
      },
      status: (signal?: AbortSignal) => api.getPlayerSync(profile.id, signal),
    }),
    [profile.id, profile.gameName, profile.tagLine, onRefresh],
  );
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<ResultFilter>("ALL");
  const [queue, setQueue] = useState<QueueFilter>("ALL");
  const visible = filterLoadedMatches(list.items, query, "ALL", result, queue);
  const highlights = loadedHighlights(list.items);
  const hasLocal = list.items.some((match) => match.localParticipantId != null);
  const ready = list.status === "ready" && list.items.length > 0;
  // Mesma página do Histórico da conta: coluna de resumo, busca e filtros, dias e cartões.
  return (
    <>
      <HistorySync onNewMatches={list.refresh} calls={calls} latestIds={latestIds} knownIds={() => list.items.map((m) => m.matchId)} onPendingChange={setPending} />
      <div className={ready ? "history-shell history-shell--aside" : "history-shell"}>
        {ready ? <aside className="history-aside"><LoadedSummary matches={list.items} subject="player" /></aside> : null}
        <div className="history-main">
          {list.items.length ? (
            <section aria-label="Busca e filtros das partidas do jogador" className="history-controls surface">
              <div className="history-search">
                <label htmlFor="player-query">Buscar campeão ou partida</label>
                <input id="player-query" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Nome do campeão ou ID da partida" aria-describedby="player-search-scope" />
                <p id="player-search-scope">Nas páginas carregadas; vale o campeão de qualquer participante.</p>
              </div>
              <div className="history-filters">
                <FilterGroup<QueueFilter> label="Fila" value={queue} onChange={setQueue} options={[["ALL", "Todas as filas"], [420, "Solo/Duo"], [440, "Flex"]] as const} />
                {hasLocal ? (
                  <FilterGroup<ResultFilter> label="Resultado" value={result} onChange={setResult} options={[["ALL", "Todos"], ["WIN", "Vitórias"], ["LOSS", "Derrotas"]] as const} />
                ) : null}
              </div>
            </section>
          ) : null}
          <section aria-labelledby="player-matches-heading" className="history-results">
            <div className="history-list-heading">
              <h3 id="player-matches-heading">Partidas importadas</h3>
              {list.items.length ? <span>{visible.length} de {count(list.items.length, "carregada", "carregadas")} neste filtro</span> : null}
            </div>
            {pending ? <SyncPendingBox pending={pending} /> : null}
            {list.status === "loading" && !pending ? <div className="history-state" role="status" aria-busy="true">Carregando partidas…</div> : null}
            {list.status === "error" ? <div className="history-state">As partidas não puderam ser carregadas agora.</div> : null}
            {list.status === "ready" && !list.items.length && !pending ? (
              <div className="empty-state">
                <h4>Nenhuma partida importada</h4>
                <p>Nenhuma partida deste jogador importada ainda. A atualização busca as ranqueadas recentes.</p>
              </div>
            ) : null}
            {list.items.length && !visible.length ? (
              <div className="empty-state">
                <h4>Nenhuma partida neste filtro</h4>
                <p>Ajuste a busca ou carregue mais páginas.</p>
              </div>
            ) : null}
            {visible.length ? <DayGroups matches={visible} highlights={highlights} /> : null}
            {list.nextCursor !== null ? (
              <button type="button" className="button button--secondary history-more" onClick={list.loadMore} disabled={list.loadingMore}>
                {list.loadingMore ? "Carregando…" : "Carregar mais partidas"}
              </button>
            ) : null}
          </section>
        </div>
      </div>
    </>
  );
}

function sameRiotId(a: { gameName: string; tagLine: string }, b: { gameName: string; tagLine: string }): boolean {
  const norm = (value: string) => value.normalize("NFC").toLocaleLowerCase("pt-BR");
  return norm(a.gameName) === norm(b.gameName) && norm(a.tagLine) === norm(b.tagLine);
}

export function PlayerPage({
  gameName,
  tagLine,
  fromMatch = null,
  previousRiotId = null,
}: {
  gameName: string;
  tagLine: string;
  /** Vindo do placar: a conta é achada pelo participante da partida, não pelo nome. */
  fromMatch?: { matchId: string; participantId: number } | null;
  /** Riot ID que o jogador usava na partida, quando mudou. */
  previousRiotId?: string | null;
}) {
  const { navigate } = useRouter();
  const [state, setState] = useState<{ profile: PlayerProfile | null; error: string | null }>({ profile: null, error: null });
  const [confirmed, confirm] = useRiotIntent();
  const [banner, setBanner] = useState<BannerFacts>({ splash: null, streak: 0 });
  useEffect(() => {
    if (!confirmed) return;
    // A página é remontada (key) a cada Riot ID: o estado inicial já é "buscando".
    const controller = new AbortController();
    const lookup = fromMatch
      ? api.lookupPlayerFromMatch(fromMatch.matchId, fromMatch.participantId, controller.signal)
      : api.lookupPlayer(gameName, tagLine, false, controller.signal);
    lookup
      .then((profile) => {
        if (fromMatch && !sameRiotId(profile, { gameName, tagLine })) {
          // Trocou de Riot ID depois da partida: o endereço passa para o nome atual.
          navigate(
            routeToPath({ name: "player", gameName: profile.gameName, tagLine: profile.tagLine, previousRiotId: `${gameName}#${tagLine}` }),
            { replace: true },
          );
          return;
        }
        rememberPlayer(profile); // buscas recentes, só neste navegador (CC-1208)
        setState({ profile, error: null });
      })
      .catch((error: unknown) => {
        if (isAbortError(error)) return;
        const message = isApiError(error) ? LOOKUP_ERRORS[error.code] : undefined;
        setState({ profile: null, error: message ?? "Não foi possível buscar o jogador agora." });
      });
    return () => controller.abort();
  }, [gameName, tagLine, confirmed, fromMatch, navigate]);
  const onRefresh = useCallback((profile: PlayerProfile) => {
    rememberPlayer(profile);
    setState({ profile, error: null });
  }, []);

  return (
    <section className="area-page player" aria-labelledby="player-heading" aria-busy={confirmed && !state.profile && !state.error}>
      {state.profile ? (
        <>
          {previousRiotId ? (
            <p className="notice notice--info" role="note">
              Na partida, este jogador aparecia como <strong>{previousRiotId}</strong>. O Riot ID mudou desde então.
            </p>
          ) : null}
          <ProfileHeader profile={state.profile} banner={banner} />
          <PlayerMatches profile={state.profile} onRefresh={onRefresh} onBanner={setBanner} />
        </>
      ) : (
        <>
          <h2 id="player-heading">
            {gameName} <span className="player-header__tag">#{tagLine}</span>
          </h2>
          {!confirmed ? (
            <RiotConfirm gameName={gameName} tagLine={tagLine} onConfirm={confirm} what="Abrir o perfil" />
          ) : state.error ? (
            <p className="notice notice--info" role="alert">{state.error}</p>
          ) : (
            <p className="muted">Buscando o jogador na Riot…</p>
          )}
        </>
      )}
    </section>
  );
}
