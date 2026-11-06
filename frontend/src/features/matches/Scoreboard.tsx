import type { MatchDetail, MatchParticipant, MatchTimeline } from "../../shared/api/client";
import { isRealMatchId, routeToPath } from "../../shared/router/routes";
import { useItemName } from "../../shared/hooks/useItemName";
import { Link } from "../../shared/router/Link";
import { ItemIcon } from "../../shared/ui/ItemIcon";
import { ChampionAvatar } from "../history/ChampionAvatar";
import { decimal, percent, roleShort } from "../history/presentation";
import { championName } from "./format";
import { BLUE, completedBuild, teamTotals } from "./matchView";

const ROLE_ORDER: Readonly<Record<string, number>> = { TOP: 0, JUNGLE: 1, MIDDLE: 2, BOTTOM: 3, UTILITY: 4 };

type DetailParticipant = MatchDetail["participants"][number];

function ordered(members: DetailParticipant[]): DetailParticipant[] {
  const rank = (p: DetailParticipant) => (p.role && p.role in ROLE_ORDER ? ROLE_ORDER[p.role] : 9);
  return [...members].sort((a, b) => rank(a) - rank(b) || a.participantId - b.participantId);
}

function Build({ items, participant }: { items: MatchTimeline["participants"][number]["items"]; participant: MatchParticipant }) {
  const itemName = useItemName();
  const official = participant.finalItems != null;
  const build = official
    ? participant.finalItems!.map((itemId) => ({ itemId }))
    : completedBuild(items ?? []);
  const trinket = official ? participant.finalTrinketItemId : null;
  const source = official ? "Inventário final oficial" : "Estimado pela linha do tempo";
  return (
    <span className="scoreboard__build" role="group" aria-label={source} title={source}>
      {build.length ? build.map((item, index) => (
          <span key={`${item.itemId}-${index}`} title={itemName({ itemId: item.itemId })}>
            <ItemIcon itemId={item.itemId} />
            <span className="sr-only">{itemName({ itemId: item.itemId })}</span>
          </span>
        )) : <span className="muted" aria-label={source}>—</span>}
      {trinket ? (
        <span className="scoreboard__trinket" role="group" aria-label="Amuleto final oficial">
          <span title={`Amuleto: ${itemName({ itemId: trinket })}`}>
            <ItemIcon itemId={trinket} />
            <span className="sr-only">{itemName({ itemId: trinket })}</span>
          </span>
        </span>
      ) : null}
    </span>
  );
}

function Row({
  match,
  participant,
  teamKills,
  timeline,
  selected,
  localId,
  fromHistory,
}: {
  match: MatchDetail;
  participant: DetailParticipant;
  teamKills: number | null;
  timeline: MatchTimeline | null;
  selected: boolean;
  localId: number | null;
  fromHistory: boolean;
}) {
  const { kills, deaths, assists } = participant;
  const scored = kills != null && deaths != null && assists != null;
  const cs =
    participant.totalMinionsKilled != null || participant.neutralMinionsKilled != null
      ? (participant.totalMinionsKilled ?? 0) + (participant.neutralMinionsKilled ?? 0)
      : null;
  const minutes = match.durationSeconds / 60;
  const role = roleShort(participant.role);
  const items = timeline?.participants.find((p) => p.participantId === participant.participantId)?.items ?? [];
  return (
    <tr className={`scoreboard__row${selected ? " scoreboard__row--selected" : ""}${participant.participantId === localId ? " scoreboard__row--local" : ""}`}>
      <th scope="row" className="scoreboard__champion">
        <Link
          to={{ name: fromHistory ? "historyMatch" : "match", matchId: match.matchId, participantId: participant.participantId }}
          className="scoreboard__link"
          aria-label={`Participante ${participant.participantId} — ${championName(participant)}`}
          aria-current={selected ? "true" : undefined}
        >
          <ChampionAvatar participant={participant} size={34} />
          <span className="scoreboard__name">
            <strong>{championName(participant)}</strong>
            <small>
              {role ?? "Função não informada"}
              {participant.participantId === localId ? <span className="scoreboard__you">Você</span> : null}
            </small>
          </span>
        </Link>
        {/* CC-1210: Riot ID como estava na partida, com link para o perfil (busca só no clique). */}
        {participant.gameName && participant.tagLine ? (
          <Link
            to={routeToPath({
              name: "player",
              gameName: participant.gameName,
              tagLine: participant.tagLine,
              // O Riot ID da partida pode ter mudado: o perfil é achado pela conta.
              ...(isRealMatchId(match.matchId) ? { fromMatch: { matchId: match.matchId, participantId: participant.participantId } } : {}),
            })}
            className="scoreboard__riot-id"
            aria-label={`Perfil de ${participant.gameName}#${participant.tagLine}`}
          >
            {participant.gameName}<span>#{participant.tagLine}</span>
          </Link>
        ) : null}
      </th>
      <td className="scoreboard__kda">
        {scored ? <>{kills} / <span className="scoreboard__deaths">{deaths}</span> / {assists}</> : "—"}
        <small>{scored ? `${decimal((kills + assists) / Math.max(1, deaths), 2)} KDA` : ""}</small>
      </td>
      <td>
        {cs ?? "—"}
        <small>{cs != null && minutes > 0 ? `${decimal(cs / minutes)}/min` : ""}</small>
      </td>
      <td>{scored && teamKills ? percent(Math.min(1, (kills + assists) / teamKills)) : "—"}</td>
      <td className="scoreboard__items"><Build items={items} participant={participant} /></td>
    </tr>
  );
}

export function Scoreboard({
  match,
  timeline,
  selected,
  localId,
  fromHistory,
}: {
  match: MatchDetail;
  timeline: MatchTimeline | null;
  selected: number | null;
  localId: number | null;
  fromHistory: boolean;
}) {
  const teams = [...new Set(match.participants.map((p) => p.team))].sort((a, b) => a - b);
  if (!teams.length) return <p className="muted">Esta partida não tem participantes registrados.</p>;
  return (
    <div className="scoreboard">
      {teams.map((team) => {
        const totals = teamTotals(match.participants, team);
        const tone = team === BLUE ? "blue" : "red";
        return (
          <table key={team} className={`scoreboard__team scoreboard__team--${tone}`}>
            <caption>
              <span className="scoreboard__caption">
              <span className="scoreboard__team-name">
                <i className={`team-key team-key--${tone}`} aria-hidden="true" />
                {team === BLUE ? "Time azul" : "Time vermelho"}
              </span>
              {totals.win != null ? <span className={`scoreboard__result scoreboard__result--${totals.win ? "win" : "loss"}`}>{totals.win ? "Vitória" : "Derrota"}</span> : null}
              {totals.kills != null ? <span className="muted">{totals.kills} / {totals.deaths} / {totals.assists}</span> : null}
              </span>
            </caption>
            <thead>
              <tr>
                <th scope="col">Campeão</th>
                <th scope="col">K / D / A</th>
                <th scope="col">Farm</th>
                <th scope="col"><abbr title="Participação em abates do time">Part. abates</abbr></th>
                <th scope="col">Itens finais</th>
              </tr>
            </thead>
            <tbody>
              {ordered(match.participants.filter((p) => p.team === team)).map((participant) => (
                <Row
                  key={participant.participantId}
                  match={match}
                  participant={participant}
                  teamKills={totals.kills}
                  timeline={timeline}
                  selected={participant.participantId === selected}
                  localId={localId}
                  fromHistory={fromHistory}
                />
              ))}
            </tbody>
          </table>
        );
      })}
      {match.participants.some((p) => p.gameName) ? (
        <p className="scoreboard__note">Riot ID como estava na partida; o jogador pode ter trocado de nome depois.</p>
      ) : null}
    </div>
  );
}
