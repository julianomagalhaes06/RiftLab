import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type MatchSummary, type PlayerProfile, type PlayerRank } from "../../shared/api/client";
import { isAbortError, isApiError } from "../../shared/api/errors";
import { usePagedList } from "../../shared/hooks/usePagedList";
import { Link } from "../../shared/router/Link";
import { routeToPath } from "../../shared/router/routes";
import { count } from "../../shared/text/count";
import { DayGroups } from "../history/HistoryPage";
import { HistorySync } from "../history/HistorySync";
import { clock } from "../history/syncMessage";
import { RankCard } from "../ranked/RankCard";
import { isTier, profileIconUrl } from "../ranked/tiers";
import { LOOKUP_ERRORS } from "./lookupErrors";
import { rememberPlayer } from "./recentPlayers";
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

function ProfileHeader({ profile }: { profile: PlayerProfile }) {
  const [broken, setBroken] = useState(false);
  const src = profileIconUrl(profile);
  return (
    <>
      <div className="player-header surface surface--pad">
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
        </div>
        {/* CC-1211: partida em andamento, como o "Live Game" do op.gg. */}
        <Link to={routeToPath({ name: "live", gameName: profile.gameName, tagLine: profile.tagLine })} className="button button--secondary player-header__live">
          <span className="player-header__live-dot" aria-hidden="true" />Partida ao vivo
        </Link>
      </div>
      <div className="player-ranks" role="group" aria-label="Elo">
        <QueueRank label="Solo/Duo" rank={profile.solo} />
        <QueueRank label="Flex" rank={profile.flex} />
      </div>
    </>
  );
}

function PlayerMatches({ profile, onRefresh }: { profile: PlayerProfile; onRefresh: (next: PlayerProfile) => void }) {
  const list = usePagedList<MatchSummary>({
    resetKey: `player-${profile.id}`,
    fetchPage: (cursor, signal) => api.listMatches({ cursor, limit: PAGE_SIZE, player: profile.id }, signal),
  });
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
  return (
    <>
      <HistorySync onNewMatches={list.refresh} calls={calls} />
      <section aria-labelledby="player-matches-heading">
        <h3 id="player-matches-heading">Partidas importadas</h3>
        {list.status === "loading" ? <p className="muted" aria-busy="true">Carregando partidas…</p> : null}
        {list.status === "error" ? <p className="muted">As partidas não puderam ser carregadas agora.</p> : null}
        {list.status === "ready" && !list.items.length ? (
          <p className="muted">Nenhuma partida deste jogador importada ainda. A atualização busca as ranqueadas recentes.</p>
        ) : null}
        {list.items.length ? (
          <>
            <p className="muted">{count(list.items.length, "partida carregada", "partidas carregadas")}</p>
            <DayGroups matches={list.items} />
          </>
        ) : null}
        {list.nextCursor !== null ? (
          <button type="button" className="button button--secondary" onClick={list.loadMore} disabled={list.loadingMore}>
            {list.loadingMore ? "Carregando…" : "Carregar mais partidas"}
          </button>
        ) : null}
      </section>
    </>
  );
}

export function PlayerPage({ gameName, tagLine }: { gameName: string; tagLine: string }) {
  const [state, setState] = useState<{ profile: PlayerProfile | null; error: string | null }>({ profile: null, error: null });
  useEffect(() => {
    // A página é remontada (key) a cada Riot ID: o estado inicial já é "buscando".
    const controller = new AbortController();
    api
      .lookupPlayer(gameName, tagLine, false, controller.signal)
      .then((profile) => {
        rememberPlayer(profile); // buscas recentes, só neste navegador (CC-1208)
        setState({ profile, error: null });
      })
      .catch((error: unknown) => {
        if (isAbortError(error)) return;
        const message = isApiError(error) ? LOOKUP_ERRORS[error.code] : undefined;
        setState({ profile: null, error: message ?? "Não foi possível buscar o jogador agora." });
      });
    return () => controller.abort();
  }, [gameName, tagLine]);
  const onRefresh = useCallback((profile: PlayerProfile) => {
    rememberPlayer(profile);
    setState({ profile, error: null });
  }, []);

  return (
    <section className="area-page player" aria-labelledby="player-heading" aria-busy={!state.profile && !state.error}>
      {state.profile ? (
        <>
          <ProfileHeader profile={state.profile} />
          <PlayerMatches profile={state.profile} onRefresh={onRefresh} />
        </>
      ) : (
        <>
          <h2 id="player-heading">
            {gameName} <span className="player-header__tag">#{tagLine}</span>
          </h2>
          {state.error ? (
            <p className="notice notice--info" role="alert">{state.error}</p>
          ) : (
            <p className="muted">Buscando o jogador na Riot…</p>
          )}
        </>
      )}
    </section>
  );
}
