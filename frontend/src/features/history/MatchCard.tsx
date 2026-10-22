import type { MatchParticipant, MatchSummary } from "../../shared/api/client";
import { Link } from "../../shared/router/Link";
import { isDemoMatchId } from "../../shared/router/routes";
import { championName } from "../matches/format";
import { ChampionAvatar } from "./ChampionAvatar";
import { useItemName } from "../../shared/hooks/useItemName";
import { ItemIcon } from "../../shared/ui/ItemIcon";
import { ItemNames } from "../../shared/ui/ItemNames";
import { count } from "../../shared/text/count";
import {
  decimal,
  localPerformance,
  matchDate,
  matchLength,
  percent,
  queueLabel,
  relativeTime,
  roleShort,
  teamsOf,
} from "./presentation";

const RESULT_TEXT = { WIN: "Vitória", LOSS: "Derrota" } as const;

/** CC-904: itens finais oficiais da conta própria, como nos sites de estatísticas. */
function FinalBuild({ items, trinket }: { items: number[]; trinket: number | null }) {
  const itemName = useItemName();
  return (
    <span className="match-card__build" role="group" aria-label="Itens finais">
      {items.map((itemId, index) => (
        <span key={`${itemId}-${index}`} title={itemName({ itemId })}>
          <ItemIcon itemId={itemId} />
          <span className="sr-only">{itemName({ itemId })}</span>
        </span>
      ))}
      {trinket ? (
        <span className="match-card__trinket" title={`Amuleto: ${itemName({ itemId: trinket })}`}>
          <ItemIcon itemId={trinket} />
          <span className="sr-only">Amuleto: {itemName({ itemId: trinket })}</span>
        </span>
      ) : null}
    </span>
  );
}

function TeamList({ team, localId, label }: { team: MatchParticipant[]; localId: number | null; label: string }) {
  return (
    <ul className="match-card__team" aria-label={label}>
      {team.map((participant) => (
        <li
          key={participant.participantId}
          className={participant.participantId === localId ? "match-card__mate match-card__mate--local" : "match-card__mate"}
        >
          <ChampionAvatar participant={participant} size={18} />
          <span>{championName(participant)}</span>
        </li>
      ))}
    </ul>
  );
}

export function MatchCard({ match, now }: { match: MatchSummary; now?: Date }) {
  const performance = localPerformance(match);
  const local = performance?.participant ?? null;
  const tone = performance?.result === "WIN" ? "win" : performance?.result === "LOSS" ? "loss" : "neutral";
  const teams = teamsOf(match);
  const demo = isDemoMatchId(match.matchId);
  const role = roleShort(local?.role);
  const title = local ? `${championName(local)} — ${queueLabel(match.queue)}` : queueLabel(match.queue);

  return (
    // CC-1205: superfície chanfrada no item e barra de resultado de 3 px no artigo (as duas
    // primitivas usam ::before, então ficam em elementos diferentes).
    <li className={`match-card match-card--${tone} surface`}>
      <article className="result-bar" data-result={tone === "neutral" ? undefined : tone} aria-label={`${title}, ${matchDate(match.startAt)}`}>
        <div className="match-card__meta">
          <strong className="match-card__queue">{queueLabel(match.queue)}</strong>
          <time dateTime={match.startAt} title={matchDate(match.startAt)}>{relativeTime(match.startAt, now)}</time>
          <span className="match-card__divider" aria-hidden="true" />
          <span className={`match-card__result${tone === "neutral" ? "" : ` result-text--${tone}`}`}>
            {performance?.result ? RESULT_TEXT[performance.result] : "Resultado não identificado"}
          </span>
          <span>{matchLength(match.durationSeconds)}</span>
        </div>

        {local && performance ? (
          <div className="match-card__hero">
            <span className="match-card__portrait">
              <ChampionAvatar participant={local} size={56} className="portrait" />
              {role ? <span className="match-card__role">{role}</span> : null}
            </span>
            <div className="match-card__score">
              <span className="match-card__champion">{championName(local)}</span>
              <span className="match-card__kda-line" aria-label={`${local.kills ?? "?"} abates, ${local.deaths ?? "?"} mortes, ${local.assists ?? "?"} assistências`}>
                {local.kills ?? "–"} <span aria-hidden="true">/</span> <span className="match-card__deaths">{local.deaths ?? "–"}</span> <span aria-hidden="true">/</span> {local.assists ?? "–"}
              </span>
              <span className="match-card__kda">{performance.kda === null ? "KDA indisponível" : `${decimal(performance.kda, 2)} KDA`}</span>
            </div>
            <dl className="match-card__stats">
              <div><dt>Part. abates</dt><dd>{performance.killParticipation === null ? "–" : percent(performance.killParticipation)}</dd></div>
              <div><dt>Farm</dt><dd>{performance.cs === null ? "–" : <>{performance.cs}{performance.csPerMinute !== null ? <small> ({decimal(performance.csPerMinute)}/min)</small> : null}</>}</dd></div>
              <div><dt>Patch</dt><dd>{match.patch}</dd></div>
            </dl>
            {local.finalItems?.length ? (
              <ItemNames version={local.champion?.dataDragonVersion ?? null}>
                <FinalBuild items={local.finalItems} trinket={local.finalTrinketItemId ?? null} />
              </ItemNames>
            ) : null}
          </div>
        ) : (
          <div className="match-card__hero match-card__hero--unknown">
            <p>
              {demo ? "Partida sintética de demonstração." : "Sua conta não foi identificada nesta partida."}
              <small> Patch {match.patch}</small>
            </p>
          </div>
        )}

        {teams.length ? (
          <div className="match-card__teams">
            {teams.map((team, index) => (
              <TeamList key={team[0]?.team ?? index} team={team} localId={local?.participantId ?? null} label={index === 0 ? "Time azul" : "Time vermelho"} />
            ))}
          </div>
        ) : null}

        <div className="match-card__actions">
          {demo ? <span className="chip origin-badge origin-badge--demo">DEMO</span> : null}
          {match.reviewPurchases && match.reviewPurchases.total > 0 ? (
            <span
              className={match.reviewPurchases.ready > 0 ? "chip chip--win review-badge" : "chip review-badge review-badge--none"}
              title="Compras do 1º ao 5º item comparadas com jogadores do mesmo campeão e rota"
            >
              {match.reviewPurchases.ready > 0
                ? `Revisão: ${match.reviewPurchases.ready} de ${count(match.reviewPurchases.total, "compra", "compras")}`
                : "Sem compras para revisar"}
            </span>
          ) : null}
          <Link
            to={{ name: "historyMatch", matchId: match.matchId, participantId: local?.participantId ?? null }}
            className="match-card__open"
            aria-label={`Contexto da partida ${match.matchId}`}
            title={match.matchId}
          >
            Analisar <span aria-hidden="true">→</span>
          </Link>
        </div>
      </article>
    </li>
  );
}
