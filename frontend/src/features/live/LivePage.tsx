import { useCallback, useEffect, useState } from "react";
import { api, type LiveGame, type LiveParticipant } from "../../shared/api/client";
import { isAbortError, isApiError } from "../../shared/api/errors";
import { Link } from "../../shared/router/Link";
import { routeToPath } from "../../shared/router/routes";
import { count } from "../../shared/text/count";
import { ChampionAvatar } from "../history/ChampionAvatar";
import { championName } from "../matches/format";
import { LOOKUP_ERRORS } from "../players/lookupErrors";
import { emblemSrc, isTier, rankTitle } from "../ranked/tiers";
import { elapsed, queueName, spellIcon, styleIcon, type LiveIcon } from "./liveAssets";
import "./live.css";

type LiveTeam = NonNullable<LiveGame["game"]>["teams"][number];

function Icon({ icon, className }: { icon: LiveIcon | null; className: string }) {
  const [broken, setBroken] = useState(false);
  if (!icon) return <span className={`${className} live-icon--empty`} aria-hidden="true" />;
  return (
    <span className={className} title={icon.name}>
      {icon.src && !broken ? <img src={icon.src} alt={icon.name} width="20" height="20" onError={() => setBroken(true)} /> : <span className="live-icon__text">{icon.name.slice(0, 2)}</span>}
      {icon.src && !broken ? null : <span className="sr-only">{icon.name}</span>}
    </span>
  );
}

function RankText({ player, queueLabel }: { player: LiveParticipant; queueLabel: string }) {
  const rank = player.rank;
  if (player.rankState === "READY" && rank && isTier(rank.tier)) {
    const games = rank.wins + rank.losses;
    return (
      <span className="live-player__rank" data-tier={rank.tier}>
        <span className="live-player__tier">
          <img src={emblemSrc(rank.tier)} alt="" width="20" height="20" />
          {rankTitle(rank.tier, rank.division)} · {rank.leaguePoints} PDL
        </span>
        <span className="live-player__record">
          {games ? `${rank.wins}V ${rank.losses}D · ${Math.round((rank.wins / games) * 100)}%` : queueLabel}
        </span>
      </span>
    );
  }
  return (
    <span className="live-player__rank" data-tier="NONE">
      <span className="live-player__tier">{player.bot ? "Bot" : player.rankState === "UNRANKED" ? "Sem classificação" : "Elo indisponível"}</span>
      <span className="live-player__record">{queueLabel}</span>
    </span>
  );
}

function PlayerRow({ player, version, queueLabel }: { player: LiveParticipant; version: string | null | undefined; queueLabel: string }) {
  const name = championName(player);
  const riotId = player.gameName && player.tagLine ? { gameName: player.gameName, tagLine: player.tagLine } : null;
  return (
    <li className={`live-player${player.requested ? " is-selected result-bar" : ""}`}>
      <ChampionAvatar participant={player} size={40} className="portrait" />
      <span className="live-player__kit" aria-label="Feitiços e runas">
        <Icon icon={spellIcon(player.spell1Id, version)} className="live-icon" />
        <Icon icon={spellIcon(player.spell2Id, version)} className="live-icon" />
        <Icon icon={styleIcon(player.perkStyle)} className="live-icon live-icon--rune" />
        <Icon icon={styleIcon(player.perkSubStyle)} className="live-icon live-icon--rune" />
      </span>
      <span className="live-player__who">
        <strong>{name}{player.requested ? <span className="sr-only"> (jogador buscado)</span> : null}</strong>
        {riotId ? (
          <Link to={routeToPath({ name: "player", ...riotId })} className="live-player__riot-id" aria-label={`${riotId.gameName}#${riotId.tagLine}`}>
            {riotId.gameName}<span>#{riotId.tagLine}</span>
          </Link>
        ) : (
          <span className="live-player__hidden">{player.bot ? "Bot" : "Riot ID não informado"}</span>
        )}
      </span>
      <RankText player={player} queueLabel={queueLabel} />
    </li>
  );
}

function TeamPanel({ team, version, queueLabel }: { team: LiveTeam; version: string | null | undefined; queueLabel: string }) {
  const blue = team.teamId === 100;
  const headingId = `live-team-${team.teamId}`;
  return (
    <section className={`live-team live-team--${blue ? "blue" : "red"}`} aria-labelledby={headingId}>
      <div className="section-head">
        <h3 id={headingId}>{blue ? "Time azul" : "Time vermelho"}</h3>
        {team.bans.length ? (
          <span className="live-team__bans">
            <span>Banimentos</span>
            <span className="live-team__ban-list">
              {team.bans.map((ban) => (
                <span key={ban.championId} className="live-team__ban" title={championName(ban)}>
                  <ChampionAvatar participant={ban} size={24} className="portrait" />
                  <span className="sr-only">{championName(ban)}</span>
                </span>
              ))}
            </span>
          </span>
        ) : null}
      </div>
      <ul className="surface row-list">
        {team.participants.map((player, index) => (
          <PlayerRow key={`${player.championId}-${index}`} player={player} version={version} queueLabel={queueLabel} />
        ))}
      </ul>
    </section>
  );
}

/** Partida ao vivo (CC-1211), como o "Live Game" do op.gg: os dois times com campeão, feitiços,
 * estilos de runa, Riot ID e elo. A Riot não dá placar ao vivo, então não há abates nem ouro. */
export function LivePage({ gameName, tagLine }: { gameName: string; tagLine: string }) {
  const [state, setState] = useState<{ live: LiveGame | null; error: string | null; loading: boolean }>({ live: null, error: null, loading: true });
  const [attempt, setAttempt] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const controller = new AbortController();
    api.getLiveGame(gameName, tagLine, controller.signal)
      .then((live) => setState({ live, error: null, loading: false }))
      .catch((error: unknown) => {
        if (isAbortError(error)) return;
        const message = isApiError(error) ? LOOKUP_ERRORS[error.code] : undefined;
        setState({ live: null, error: message ?? "Não foi possível consultar a partida ao vivo agora.", loading: false });
      });
    return () => controller.abort();
  }, [gameName, tagLine, attempt]);
  const game = state.live?.game ?? null;
  useEffect(() => {
    if (!game?.startedAt) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [game?.startedAt]);
  const retry = useCallback(() => {
    setState((current) => ({ ...current, loading: true }));
    setAttempt((value) => value + 1);
  }, []);

  const shownName = state.live?.gameName ?? gameName;
  const shownTag = state.live?.tagLine ?? tagLine;
  const clock = elapsed(game?.startedAt, now);
  const queueLabel = game?.rankQueue === "RANKED_FLEX_SR" ? "Elo da Flex" : "Elo da Solo/Duo";
  const players = game ? game.teams.reduce((total, team) => total + team.participants.length, 0) : 0;

  return (
    <section className="live-page" aria-labelledby="live-heading" aria-busy={state.loading}>
      <header className="page-head live-head">
        <div>
          <p className="page-head__context">Partida ao vivo · BR1</p>
          <h2 id="live-heading">{shownName} <span className="live-head__tag">#{shownTag}</span></h2>
          <p className="page-head__lead">
            {game ? <>
              <strong>{queueName(game.queueId, game.gameMode)}</strong>
              {" · "}{clock ? <>{clock} de jogo</> : "carregando a partida"}
              {" · "}{count(players, "jogador", "jogadores")}
            </> : "Composição da partida em andamento, como na tela de carregamento. Sem placar: a Riot não informa abates nem ouro ao vivo."}
          </p>
        </div>
        <div className="live-head__actions">
          <button type="button" className="button button--secondary" onClick={retry} disabled={state.loading}>
            {state.loading ? "Verificando…" : "Verificar de novo"}
          </button>
          <Link to={routeToPath({ name: "player", gameName: shownName, tagLine: shownTag })} className="arrow-link">Ver perfil</Link>
        </div>
      </header>

      {state.loading && !state.live && !state.error ? <p className="live-state surface" role="status">Procurando partida em andamento…</p> : null}
      {state.error ? <p className="live-state surface" role="alert">{state.error}</p> : null}
      {state.live?.state === "NOT_IN_GAME" ? (
        <div className="live-state surface" role="status">
          <strong>{shownName}#{shownTag} não está em partida agora.</strong>
          <p>A Riot só mostra a partida depois da tela de carregamento; na seleção de campeões ela ainda não aparece.</p>
        </div>
      ) : null}
      {game ? (
        <div className="live-teams">
          {game.teams.map((team) => <TeamPanel key={team.teamId} team={team} version={game.dataDragonVersion} queueLabel={queueLabel} />)}
        </div>
      ) : null}
      {game ? <p className="live-note">Elo da fila {game.rankQueue === "RANKED_FLEX_SR" ? "Flex" : "Solo/Duo"} de cada jogador, consultado agora. A composição fica 20 s em memória e nada é gravado.</p> : null}
    </section>
  );
}
