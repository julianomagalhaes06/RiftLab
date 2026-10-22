import type { MatchDetail, MatchTimeline } from "../../shared/api/client";
import { isDemoMatchId } from "../../shared/router/routes";
import { ChampionAvatar } from "../history/ChampionAvatar";
import { matchDate, matchLength, queueLabel, relativeTime } from "../history/presentation";
import { championName } from "./format";
import { BLUE, objectiveCounts, RED, teamTotals, type ObjectiveCounts } from "./matchView";

const OBJECTIVES: Array<[keyof ObjectiveCounts, string]> = [
  ["TOWER", "Torres"],
  ["DRAGON", "Dragões"],
  ["VOIDGRUB", "Larvas"],
  ["HERALD", "Arautos"],
  ["BARON", "Barões"],
  ["ATAKHAN", "Atakhan"],
  ["INHIBITOR", "Inibidores"],
];

function Objectives({ timeline, team }: { timeline: MatchTimeline | null; team: number }) {
  if (!timeline) return null;
  const counts = objectiveCounts(timeline.objectives, team);
  const shown = OBJECTIVES.filter(([kind]) => counts[kind] > 0 || kind === "TOWER" || kind === "DRAGON" || kind === "BARON");
  return (
    <ul className="match-hero__objectives" aria-label={`Objetivos do time ${team === BLUE ? "azul" : "vermelho"}`}>
      {shown.map(([kind, label]) => (
        <li key={kind}><span>{label}</span><strong>{counts[kind]}</strong></li>
      ))}
    </ul>
  );
}

/** Topo da tela da partida: resultado, fila, placar dos times e objetivos. */
export function MatchHeader({ match, timeline, localId }: { match: MatchDetail; timeline: MatchTimeline | null; localId: number | null }) {
  const local = match.participants.find((p) => p.participantId === localId) ?? null;
  const result = local?.win == null ? null : local.win ? "Vitória" : "Derrota";
  const tone = result === "Vitória" ? "win" : result === "Derrota" ? "loss" : "neutral";
  const blue = teamTotals(match.participants, BLUE);
  const red = teamTotals(match.participants, RED);
  const demo = isDemoMatchId(match.matchId);
  return (
    // CC-1206: superfície do padrão; barra de resultado de 3 px no ::after (o ::before é o fundo).
    <header className={`match-hero match-hero--${tone} surface`} data-result={tone === "neutral" ? undefined : tone}>
      <div className="match-hero__main">
        {local ? <ChampionAvatar participant={local} size={64} className="portrait match-hero__portrait" /> : null}
        <div>
          <h2 id="match-heading">
            <span className="sr-only">Partida {match.matchId}: </span>
            {result ? <span className="match-hero__result">{result}</span> : null}
            {result ? " · " : null}
            {queueLabel(match.queue)}
          </h2>
          <p className="match-hero__meta">
            {local ? <span>Você jogou de <strong>{championName(local)}</strong></span> : null}
            <time dateTime={match.startAt} title={matchDate(match.startAt)}>{relativeTime(match.startAt)}</time>
            <span>{matchLength(match.durationSeconds)}</span>
            <span>Patch {match.patch}</span>
            {demo ? <span className="chip">Demonstração</span> : null}
            {match.supportLevel === "LIMITED" ? (
              <span className="chip match-hero__pill" title="Ainda não há avaliação de ranking própria neste patch. Nenhuma recomendação é gerada.">
                Patch sem avaliação própria
              </span>
            ) : null}
          </p>
        </div>
      </div>
      <div className="match-hero__score" aria-label="Placar de abates">
        <div className="match-hero__team match-hero__team--blue">
          <span className="match-hero__team-name"><i className="team-key team-key--blue" aria-hidden="true" />Azul</span>
          <strong>{blue.kills ?? "—"}</strong>
          <Objectives timeline={timeline} team={BLUE} />
        </div>
        <span className="match-hero__versus" aria-hidden="true">×</span>
        <div className="match-hero__team match-hero__team--red">
          <span className="match-hero__team-name"><i className="team-key team-key--red" aria-hidden="true" />Vermelho</span>
          <strong>{red.kills ?? "—"}</strong>
          <Objectives timeline={timeline} team={RED} />
        </div>
      </div>
      {demo ? <p className="match-hero__note">Partida sintética: valida o fluxo do sistema e não representa um jogo real.</p> : null}
    </header>
  );
}
