import { useId, useState, type ReactNode } from "react";
import type { MatchParticipant, MatchSummary } from "../../shared/api/client";
import { Link } from "../../shared/router/Link";
import { isDemoMatchId } from "../../shared/router/routes";
import { championName } from "../matches/format";
import { ChampionAvatar } from "./ChampionAvatar";
import { useItemName } from "../../shared/hooks/useItemName";
import { ItemIcon } from "../../shared/ui/ItemIcon";
import { ItemNames } from "../../shared/ui/ItemNames";
import { count } from "../../shared/text/count";
import { GameIcon } from "../../shared/ui/GameIcon";
import {
  HIGHLIGHT_TEXT,
  type HighlightKind,
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

/** CC-904: itens finais oficiais, em grade 3 × 2 com o amuleto ao lado (como nos sites de
 * estatísticas). Espaços vazios ficam marcados para a grade não mudar de largura. */
function FinalBuild({ items, trinket, size = "md" }: { items: number[]; trinket: number | null; size?: "md" | "sm" }) {
  const itemName = useItemName();
  const slots = [...items.slice(0, 6), ...Array.from({ length: Math.max(0, 6 - items.length) }, () => null)];
  return (
    <span className={`match-build match-build--${size}`} role="group" aria-label="Itens finais">
      <span className="match-build__grid">
        {slots.map((itemId, index) => itemId ? (
          <span key={`${itemId}-${index}`} title={itemName({ itemId })}>
            <ItemIcon itemId={itemId} />
            <span className="sr-only">{itemName({ itemId })}</span>
          </span>
        ) : <span key={`empty-${index}`} className="match-build__empty" aria-hidden="true" />)}
      </span>
      {trinket ? (
        <span className="match-build__trinket" title={`Amuleto: ${itemName({ itemId: trinket })}`}>
          <ItemIcon itemId={trinket} />
          <span className="sr-only">Amuleto: {itemName({ itemId: trinket })}</span>
        </span>
      ) : null}
    </span>
  );
}

/** Time em uma faixa de cinco retratos; o nome fica no texto acessível e na dica. */
function TeamStrip({ team, localId, label, side }: { team: MatchParticipant[]; localId: number | null; label: string; side: "blue" | "red" }) {
  return (
    <ul className={`match-card__team match-card__team--${side}`} aria-label={label}>
      {team.map((participant) => {
        const local = participant.participantId === localId;
        return (
          <li key={participant.participantId} className={local ? "match-card__mate match-card__mate--local" : "match-card__mate"} title={championName(participant)}>
            <ChampionAvatar participant={participant} size={24} />
            <span className="sr-only">{championName(participant)}{local ? " (você)" : ""}</span>
          </li>
        );
      })}
    </ul>
  );
}

function kdaOf(participant: MatchParticipant): string {
  const { kills, deaths, assists } = participant;
  return kills == null || deaths == null || assists == null ? "–" : `${kills} / ${deaths} / ${assists}`;
}

/** Placar das duas equipes, aberto dentro do cartão (como a linha expandida do op.gg). */
function MatchDetails({ id, teams, localId, version }: { id: string; teams: MatchParticipant[][]; localId: number | null; version: string | null }) {
  return (
    <div id={id} className="match-details">
      <ItemNames version={version}>
        {teams.map((team, index) => {
          const side = index === 0 ? "blue" : "red";
          const result = team[0]?.win == null ? null : team[0].win ? "WIN" : "LOSS";
          return (
            <table key={team[0]?.team ?? index} className={`match-details__team match-details__team--${side}`}>
              <caption>
                <span>{index === 0 ? "Time azul" : "Time vermelho"}</span>
                {result ? " " : null}
                {result ? <span className={`result-text--${result === "WIN" ? "win" : "loss"}`}>{RESULT_TEXT[result]}</span> : null}
              </caption>
              <thead>
                <tr><th scope="col">Campeão</th><th scope="col">KDA</th><th scope="col">Farm</th><th scope="col" className="match-details__items">Itens</th></tr>
              </thead>
              <tbody>
                {team.map((participant) => {
                  const local = participant.participantId === localId;
                  const cs = participant.totalMinionsKilled != null || participant.neutralMinionsKilled != null
                    ? (participant.totalMinionsKilled ?? 0) + (participant.neutralMinionsKilled ?? 0) : null;
                  return (
                    <tr key={participant.participantId} className={local ? "is-local" : undefined}>
                      <th scope="row">
                        <span className="match-details__who">
                          <ChampionAvatar participant={participant} size={28} className="portrait" />
                          <span>{championName(participant)}{local ? <small> você</small> : null}</span>
                        </span>
                      </th>
                      <td className="match-details__num">{kdaOf(participant)}</td>
                      <td className="match-details__num">{cs ?? "–"}</td>
                      <td className="match-details__items">{participant.finalItems?.length
                        ? <FinalBuild items={participant.finalItems} trinket={participant.finalTrinketItemId ?? null} size="sm" />
                        : <span className="muted">–</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          );
        })}
      </ItemNames>
    </div>
  );
}

/** Número em destaque (maior da série carregada): cor dourada, estrela e o motivo na dica e no
 * texto acessível. */
function Best({ kind, on, children }: { kind: HighlightKind; on: boolean; children: ReactNode }) {
  if (!on) return <>{children}</>;
  return (
    <span className="is-best" title={HIGHLIGHT_TEXT[kind]}>
      {children}<GameIcon name="star" size={12} className="is-best__star" /><span className="sr-only"> ({HIGHLIGHT_TEXT[kind]})</span>
    </span>
  );
}

export function MatchCard({ match, now, highlights = [] }: { match: MatchSummary; now?: Date; highlights?: HighlightKind[] }) {
  const performance = localPerformance(match);
  const local = performance?.participant ?? null;
  const tone = performance?.result === "WIN" ? "win" : performance?.result === "LOSS" ? "loss" : "neutral";
  const teams = teamsOf(match);
  const demo = isDemoMatchId(match.matchId);
  const role = roleShort(local?.role);
  const title = local ? `${championName(local)} — ${queueLabel(match.queue)}` : queueLabel(match.queue);
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const version = local?.champion?.dataDragonVersion ?? teams.flat().find((p) => p.champion?.dataDragonVersion)?.champion?.dataDragonVersion ?? null;
  const canExpand = teams.length > 0 && teams.flat().some((p) => p.kills != null);

  return (
    // Fundo levemente tingido pelo resultado (padrão dos sites de estatísticas) sobre a superfície
    // chanfrada; a barra de 3 px fica no artigo (as duas primitivas usam ::before).
    <li className={`match-card match-card--${tone}${canExpand ? " match-card--expandable" : ""} surface`}>
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
              <span className="match-card__kda">{performance.kda === null ? "KDA indisponível" : <Best kind="kda" on={highlights.includes("kda")}>{decimal(performance.kda, 2)} KDA</Best>}</span>
            </div>
            {local.finalItems?.length ? (
              <ItemNames version={local.champion?.dataDragonVersion ?? null}>
                <FinalBuild items={local.finalItems} trinket={local.finalTrinketItemId ?? null} />
              </ItemNames>
            ) : null}
            <dl className="match-card__stats">
              <div><dt>Part. abates</dt><dd>{performance.killParticipation === null ? "–" : <Best kind="kp" on={highlights.includes("kp")}>{percent(performance.killParticipation)}</Best>}</dd></div>
              <div><dt>Farm</dt><dd>{performance.cs === null ? "–" : <Best kind="cs" on={highlights.includes("cs")}>{performance.cs}{performance.csPerMinute !== null ? <small> ({decimal(performance.csPerMinute)}/min)</small> : null}</Best>}</dd></div>
            </dl>
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
              <TeamStrip key={team[0]?.team ?? index} team={team} localId={local?.participantId ?? null}
                label={index === 0 ? "Time azul" : "Time vermelho"} side={index === 0 ? "blue" : "red"} />
            ))}
          </div>
        ) : null}

        <div className="match-card__actions">
          <Link
            to={{ name: "historyMatch", matchId: match.matchId, participantId: local?.participantId ?? null }}
            className="match-card__open"
            aria-label={`Contexto da partida ${match.matchId}`}
            title={match.matchId}
          >
            Analisar <span aria-hidden="true">→</span>
          </Link>
          {match.reviewPurchases && match.reviewPurchases.total > 0 ? (
            <span
              className={match.reviewPurchases.ready > 0 ? "review-badge review-badge--ready" : "review-badge review-badge--none"}
              title="Compras do 1º ao 5º item comparadas com jogadores do mesmo campeão e rota"
            >
              {match.reviewPurchases.ready > 0
                ? `Revisão: ${match.reviewPurchases.ready} de ${count(match.reviewPurchases.total, "compra", "compras")}`
                : "Sem compras para revisar"}
            </span>
          ) : null}
          {demo ? <span className="chip origin-badge origin-badge--demo">DEMO</span> : null}
        </div>
      </article>
      {/* Faixa de expandir na borda direita, na tinta do resultado (como no u.gg). */}
      {canExpand ? (
        <button type="button" className="match-card__toggle" aria-expanded={open} aria-controls={detailsId}
          aria-label={open ? "Ocultar placar da partida" : "Mostrar placar da partida"} onClick={() => setOpen(!open)}>
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6" /></svg>
        </button>
      ) : null}
      {open ? <MatchDetails id={detailsId} teams={teams} localId={local?.participantId ?? null} version={version} /> : null}
    </li>
  );
}
