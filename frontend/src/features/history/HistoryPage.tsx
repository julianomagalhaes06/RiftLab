import { useCallback, useEffect, useState } from "react";
import { api, type LearningProgress, type MatchSummary, type PlayerProfile } from "../../shared/api/client";
import { usePagedList } from "../../shared/hooks/usePagedList";
import { Link } from "../../shared/router/Link";
import { isDemoMatchId } from "../../shared/router/routes";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { championName, championSplashUrl } from "../matches/format";
import { GameIcon } from "../../shared/ui/GameIcon";
import { betterRank, emblemSrc, isTier, profileIconUrl, rankTitle } from "../ranked/tiers";
import { ChampionAvatar } from "./ChampionAvatar";
import { filterLoadedMatches, type OriginFilter, type QueueFilter, type ResultFilter } from "./filter";
import { HistorySync, type SyncPending } from "./HistorySync";
import { SyncPendingBox } from "./SyncPendingBox";
import { MatchCard } from "./MatchCard";
import { dayKey, dayLabel, decimal, localPerformance, percent, record, roleShort, summarizeLoaded, winStreak } from "./presentation";
import { SplashArt } from "../../shared/ui/SplashArt";
import "./history.css";
import { count } from "../../shared/text/count";
import { useDemoNotice } from "../../app/demoNotice";
import { useAuth } from "../auth/authContext";

const PAGE_SIZE = 20;

function FilterGroup<T extends string | number>({
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

/** Conquistas das partidas carregadas (só dado real): maior KDA, mais farm por minuto e maior
 * participação em abates, marcadas no cartão quando há ao menos três partidas da conta. */
function loadedHighlights(matches: MatchSummary[]): Record<string, string[]> {
  const performances = matches.flatMap((match) => {
    const performance = localPerformance(match);
    return performance ? [{ matchId: match.matchId, performance }] : [];
  });
  if (performances.length < 3) return {};
  const result: Record<string, string[]> = {};
  const mark = (label: string, pick: (p: (typeof performances)[number]["performance"]) => number | null) => {
    let best: { matchId: string; value: number } | null = null;
    for (const { matchId, performance } of performances) {
      const value = pick(performance);
      if (value !== null && (best === null || value > best.value)) best = { matchId, value };
    }
    if (best) (result[best.matchId] ??= []).push(label);
  };
  mark("Maior KDA", (p) => p.kda);
  mark("Mais farm por minuto", (p) => p.csPerMinute);
  mark("Maior participação em abates", (p) => p.killParticipation);
  return result;
}

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
          </div>
        </div>
        <div className="history-banner__side">
          {streak >= 2 ? <span className="hud-tag hud-tag--hot"><GameIcon name="flame" size={14} /> {streak} vitórias seguidas</span> : null}
          {/* CC-1202: a importação manual saiu do topo e fica junto das partidas. */}
          <Link to="/importar" className="arrow-link history-banner__import">Importar partida</Link>
        </div>
      </div>
    </header>
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

function LoadedSummary({ matches }: { matches: MatchSummary[] }) {
  const { demoEnabled } = useAuth();
  const summary = summarizeLoaded(matches);
  const demoCount = matches.filter((match) => isDemoMatchId(match.matchId)).length;
  if (!summary.games) {
    return (
      <section className="history-summary surface surface--pad" aria-label="Resumo das páginas carregadas">
        <strong>{count(matches.length, "partida carregada", "partidas carregadas")}</strong>
        {demoEnabled ? <span>{matches.length - demoCount} RIOT · {demoCount} DEMO</span> : null}
        <p>Sua conta não foi identificada nas partidas carregadas; resultado e KDA dependem da observação de elo da conta configurada.</p>
      </section>
    );
  }
  const rate = summary.wins + summary.losses ? summary.wins / (summary.wins + summary.losses) : null;
  // Coluna de resumo como nos perfis do tracker.gg e do u.gg: desempenho, forma recente, campeões e rotas.
  return (
    <section className="history-overview" aria-label="Resumo das suas partidas carregadas">
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
      <p className="history-overview__scope">Calculado só nas páginas carregadas ({count(matches.length, "partida", "partidas")}, {demoCount} DEMO).</p>
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
          <p id="history-search-scope">Nas páginas carregadas; vale o campeão de qualquer participante.</p>
        </div>
        <div className="history-filters">
          <FilterGroup<QueueFilter> label="Fila" value={queue} onChange={setQueue} options={[["ALL", "Todas as filas"], [420, "Solo/Duo"], [440, "Flex"]] as const} />
          {hasLocal ? (
            <FilterGroup<ResultFilter> label="Resultado" value={result} onChange={setResult} options={[["ALL", "Todos"], ["WIN", "Vitórias"], ["LOSS", "Derrotas"]] as const} />
          ) : null}
          {demoEnabled ? <FilterGroup<OriginFilter> label="Origem da partida" value={origin} onChange={setOrigin} options={[["ALL", "Minha conta e DEMO"], ["RIOT", "Minha conta"], ["DEMO", "DEMO"]] as const} /> : null}
        </div>
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
