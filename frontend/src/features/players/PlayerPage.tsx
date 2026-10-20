import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type MatchSummary, type PlayerProfile, type PlayerRank } from "../../shared/api/client";
import { isAbortError, isApiError } from "../../shared/api/errors";
import { usePagedList } from "../../shared/hooks/usePagedList";
import { count } from "../../shared/text/count";
import { DayGroups } from "../history/HistoryPage";
import { HistorySync } from "../history/HistorySync";
import { clock } from "../history/syncMessage";
import "../history/history.css";
import "./players.css";

const PAGE_SIZE = 20;
const TIER_NAMES: Record<string, string> = {
  IRON: "Ferro", BRONZE: "Bronze", SILVER: "Prata", GOLD: "Ouro",
  PLATINUM: "Platina", EMERALD: "Esmeralda", DIAMOND: "Diamante",
  MASTER: "Mestre", GRANDMASTER: "Grão-mestre", CHALLENGER: "Desafiante",
};
const LOOKUP_ERRORS: Record<string, string> = {
  PLAYER_NOT_FOUND: "Nenhuma conta Riot com esse Riot ID. Confira o nome e a TAG.",
  PLAYER_NOT_ON_PLATFORM: "A conta existe, mas não joga League of Legends no servidor BR.",
  INVALID_RIOT_ID: "Use o formato Nome#TAG (a TAG tem de 3 a 5 letras ou números).",
  RIOT_KEY_MISSING: "Busca indisponível: o servidor está sem chave da Riot.",
  SECRET_MISSING: "Busca indisponível: o servidor está sem o segredo local de identificação.",
  RIOT_AUTH_FAILED: "A chave da Riot foi recusada (a de desenvolvimento expira em 24 h).",
  RIOT_RATE_LIMITED: "Muitas consultas à Riot agora. Tente de novo em instantes.",
};

function iconUrl(profile: PlayerProfile): string | null {
  if (profile.profileIconId == null || !profile.dataDragonVersion) return null;
  return `https://ddragon.leagueoflegends.com/cdn/${profile.dataDragonVersion}/img/profileicon/${profile.profileIconId}.png`;
}

function RankLine({ label, rank }: { label: string; rank: PlayerRank | null | undefined }) {
  if (!rank) return <li><span>{label}</span> <span className="muted">sem observação</span></li>;
  if (rank.state === "UNRANKED" || !rank.tier) return <li><span>{label}</span> <span className="muted">Sem classificação</span></li>;
  const tier = TIER_NAMES[rank.tier] ?? rank.tier;
  const games = (rank.wins ?? 0) + (rank.losses ?? 0);
  return (
    <li>
      <span>{label}</span>
      <img src={`/ranked-emblems/${rank.tier.toLowerCase()}.webp`} alt="" width="28" height="28" />
      <strong>{tier}{rank.division && !["MASTER", "GRANDMASTER", "CHALLENGER"].includes(rank.tier) ? ` ${rank.division}` : ""}</strong>
      <span>{rank.leaguePoints ?? 0} PDL</span>
      {games ? (
        <span className="muted">
          {rank.wins}V {rank.losses}D · {Math.round(((rank.wins ?? 0) / games) * 100)}%
        </span>
      ) : null}
    </li>
  );
}

function ProfileHeader({ profile }: { profile: PlayerProfile }) {
  const [broken, setBroken] = useState(false);
  const src = iconUrl(profile);
  return (
    <div className="player-header">
      <span className="player-header__icon">
        {src && !broken ? (
          <img src={src} alt="" width="88" height="88" onError={() => setBroken(true)} />
        ) : (
          <span aria-hidden="true">{profile.gameName.slice(0, 1).toUpperCase()}</span>
        )}
        {profile.summonerLevel != null ? <span className="player-header__level">{profile.summonerLevel}</span> : null}
      </span>
      <div className="player-header__who">
        <h2 id="player-heading">
          {profile.gameName} <span className="player-header__tag">#{profile.tagLine}</span>
        </h2>
        <p className="muted">
          {profile.summonerLevel != null ? `Nível ${profile.summonerLevel} · ` : ""}
          Atualizado às {clock(profile.refreshedAt) ?? "–"}
          {profile.staleReason === "RIOT_KEY_MISSING" ? " (sem chave da Riot para atualizar)" : ""}
        </p>
        <ul className="player-header__ranks" aria-label="Elo">
          <RankLine label="Solo/Duo" rank={profile.solo} />
          <RankLine label="Flex" rank={profile.flex} />
        </ul>
      </div>
    </div>
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
      .then((profile) => setState({ profile, error: null }))
      .catch((error: unknown) => {
        if (isAbortError(error)) return;
        const message = isApiError(error) ? LOOKUP_ERRORS[error.code] : undefined;
        setState({ profile: null, error: message ?? "Não foi possível buscar o jogador agora." });
      });
    return () => controller.abort();
  }, [gameName, tagLine]);
  const onRefresh = useCallback((profile: PlayerProfile) => setState({ profile, error: null }), []);

  return (
    <section className="section player" aria-labelledby="player-heading" aria-busy={!state.profile && !state.error}>
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
