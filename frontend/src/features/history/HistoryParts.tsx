import type { MatchSummary } from "../../shared/api/client";
import { isDemoMatchId } from "../../shared/router/routes";
import { count } from "../../shared/text/count";
import { GameIcon } from "../../shared/ui/GameIcon";
import { useAuth } from "../auth/authContext";
import { championName } from "../matches/format";
import { ChampionAvatar } from "./ChampionAvatar";
import { MatchCard } from "./MatchCard";
import { dayKey, dayLabel, decimal, percent, record, roleShort, summarizeLoaded, winStreak } from "./presentation";
import "./history.css";

/** Peças do Histórico usadas também no perfil do jogador pesquisado: filtros segmentados,
 * coluna de resumo e partidas agrupadas por dia. */

export function FilterGroup<T extends string | number>({
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

/** Anel da taxa de vitória (verde = vitórias, vermelho = derrotas), decorativo: o número fica ao lado. */
function WinRing({ rate }: { rate: number }) {
  const radius = 34;
  const length = 2 * Math.PI * radius;
  return (
    <svg className="win-ring" viewBox="0 0 84 84" width="84" height="84" aria-hidden="true">
      <circle cx="42" cy="42" r={radius} className="win-ring__loss" />
      <circle cx="42" cy="42" r={radius} className="win-ring__win" strokeDasharray={`${rate * length} ${length}`} transform="rotate(-90 42 42)" />
    </svg>
  );
}

/** Coluna de resumo das partidas carregadas: da conta (Histórico) ou do jogador pesquisado. */
export function LoadedSummary({ matches, subject = "account" }: { matches: MatchSummary[]; subject?: "account" | "player" }) {
  const { demoEnabled } = useAuth();
  const player = subject === "player";
  const summary = summarizeLoaded(matches);
  const demoCount = matches.filter((match) => isDemoMatchId(match.matchId)).length;
  if (!summary.games) {
    return (
      <section className="history-summary surface surface--pad" aria-label="Resumo das páginas carregadas">
        <strong>{count(matches.length, "partida carregada", "partidas carregadas")}</strong>
        {demoEnabled && !player ? <span>{matches.length - demoCount} RIOT · {demoCount} DEMO</span> : null}
        <p>{player
          ? "O jogador não foi identificado nas partidas carregadas; resultado e KDA aparecem quando ele for reconhecido no placar."
          : "Sua conta não foi identificada nas partidas carregadas; resultado e KDA dependem da observação de elo da conta configurada."}</p>
      </section>
    );
  }
  const rate = summary.wins + summary.losses ? summary.wins / (summary.wins + summary.losses) : null;
  // Coluna de resumo como nos perfis do tracker.gg e do u.gg: desempenho, forma recente, campeões e rotas.
  return (
    <section className="history-overview" aria-label={player ? "Resumo das partidas carregadas do jogador" : "Resumo das suas partidas carregadas"}>
      <div className="history-card surface">
        <h3 className="history-card__title"><GameIcon name="trophy" size={16} /> Desempenho</h3>
        <div className="history-perf">
          {rate !== null ? <WinRing rate={rate} /> : null}
          <div className="metric">
            <p className="metric__label">Taxa de vitória</p>
            <p className="metric__value">{rate === null ? "—" : percent(rate)}</p>
            <p className="metric__foot"><span className="result-text--win">{summary.wins}V</span> <span className="result-text--loss">{summary.losses}D</span></p>
          </div>
        </div>
        <div className="history-perf__kda">
          <div>
            <p className="metric__label">KDA</p>
            <p className="history-perf__value">{summary.kda === null ? "—" : decimal(summary.kda, 2)}</p>
          </div>
          {summary.averages ? (
            <p className="metric__foot">{decimal(summary.averages.kills)} / {decimal(summary.averages.deaths)} / {decimal(summary.averages.assists)} em média</p>
          ) : null}
        </div>
        {winStreak(summary.recent) >= 2 ? (
          <p className="history-streak"><GameIcon name="flame" size={16} /> {winStreak(summary.recent)} vitórias seguidas</p>
        ) : null}
        {summary.recent.length ? (
          <div className="history-form">
            <p className="metric__label">Últimas {count(summary.recent.length, "partida", "partidas")}</p>
            <ol className="history-form__list" aria-label="Resultados recentes, da partida mais nova para a mais antiga">
              {summary.recent.map((result, index) => (
                <li key={index} className={`history-form__dot history-form__dot--${result === "WIN" ? "win" : "loss"}`} title={result === "WIN" ? "Vitória" : "Derrota"}>
                  <span aria-hidden="true">{result === "WIN" ? "V" : "D"}</span>
                  <span className="sr-only">{result === "WIN" ? "Vitória" : "Derrota"}</span>
                </li>
              ))}
            </ol>
          </div>
        ) : null}
      </div>

      <div className="history-card surface">
        <h3 className="history-card__title"><GameIcon name="sword" size={16} /> Campeões mais jogados</h3>
        <ul className="history-champions" aria-label="Campeões mais jogados nas partidas carregadas">
          {summary.champions.map((champion) => {
            const decided = champion.wins + champion.losses;
            return (
              <li key={champion.participant.championId}>
                <ChampionAvatar participant={champion.participant} size={36} className="portrait" />
                <span className="history-champions__name">
                  <strong>{championName(champion.participant)}</strong>
                  <small>{champion.wins}V {champion.losses}D · {count(champion.games, "jogo", "jogos")}</small>
                </span>
                <span className="history-champions__nums">
                  <strong>{decided ? percent(champion.wins / decided) : "—"}</strong>
                  <small>{champion.kda === null ? "KDA —" : `${decimal(champion.kda, 2)} KDA`}</small>
                </span>
              </li>
            );
          })}
        </ul>
      </div>

      {summary.roles.length ? (
        <div className="history-card surface">
          <h3 className="history-card__title"><GameIcon name="map" size={16} /> Rotas</h3>
          <ul className="history-roles" aria-label="Rotas jogadas nas partidas carregadas">
            {summary.roles.map((role) => {
              const decided = role.wins + role.losses;
              return (
                <li key={role.role}>
                  <span className="history-roles__name">{roleShort(role.role)}</span>
                  <span className="history-roles__bar" aria-hidden="true"><span style={{ width: `${(role.games / summary.games) * 100}%` }} /></span>
                  <span className="history-roles__nums">{count(role.games, "jogo", "jogos")}{decided ? ` · ${percent(role.wins / decided)}` : ""}</span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
      <p className="history-overview__scope">Calculado só nas páginas carregadas ({count(matches.length, "partida", "partidas")}{player ? "" : `, ${demoCount} DEMO`}).</p>
    </section>
  );
}

export function DayGroups({ matches, highlights = {} }: { matches: MatchSummary[]; highlights?: Record<string, string[]> }) {
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
        const dayKda = summarizeLoaded(group.items).kda;
        return (
          <section key={group.key} className="history-day" aria-label={group.label}>
            <h4 className="history-day__heading">
              <span>{group.label}</span>
              <span className="history-day__count">{group.items.length}</span>
              {day.games ? (
                <small>
                  <span className="result-text--win">{day.wins}V</span> <span className="result-text--loss">{day.losses}D</span> · {percent(day.wins / day.games)}
                </small>
              ) : null}
              {dayKda !== null ? <small className="history-day__kda">KDA médio {decimal(dayKda, 2)}</small> : null}
            </h4>
            <ul className="history-list" aria-label={`Partidas de ${group.label}`}>
              {group.items.map((match) => <MatchCard key={match.matchId} match={match} now={now} highlights={highlights[match.matchId]} />)}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
