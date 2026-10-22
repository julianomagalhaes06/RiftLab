import { useEffect, useState } from "react";
import { api, type Dashboard as DashboardData, type DashboardQueueParam } from "../../shared/api/client";
import { isAbortError } from "../../shared/api/errors";
import { Link } from "../../shared/router/Link";
import { ChampionAvatar } from "../history/ChampionAvatar";
import { RecentMatches } from "../history/RecentMatches";
import { matchDuration, queueLabel, shortWhen } from "../history/presentation";
import { championName } from "../matches/format";
import { MyRank } from "../ranked/MyRank";
import { count } from "../../shared/text/count";
import "./dashboard.css";
import { useDemoNotice } from "../../app/demoNotice";

const number = (value: number) => value.toLocaleString("pt-BR", { maximumFractionDigits: 2 });
const QUEUE_NAME: Record<number, string> = { 420: "Solo/Duo", 440: "Flex" };

/** Diz qual fila está no recorte e, na escolha automática, por quê (CC-904). */
function queueSentence(data: DashboardData): string {
  const name = QUEUE_NAME[data.queue] ?? `fila ${data.queue}`;
  const other = data.queues.find((entry) => entry.queue !== data.queue && entry.totalMatches > 0);
  const base = `Estatísticas descritivas da fila ${name}`;
  if (data.queueSelection === "AUTO") {
    return `${base}, a da sua partida importada mais recente.${other ? ` Também há ${count(other.totalMatches, "partida", "partidas")} em ${QUEUE_NAME[other.queue] ?? other.queue}.` : ""}`;
  }
  return `${base}.`;
}

/** Linha de contexto do cabeçalho: o recorte que as métricas descrevem. */
function contextLine(data: DashboardData | null): string {
  if (!data || data.status !== "READY") return "Painel";
  const origin = data.origin === "DEMO" ? "demonstração sintética" : "conta própria";
  return `Painel · últimas ${count(data.sampledMatches, "partida", "partidas")} · ${QUEUE_NAME[data.queue] ?? `fila ${data.queue}`} · ${origin}`;
}

function EyeIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function RecentRow({ match, queue }: { match: DashboardData["recentMatches"][number]; queue: number }) {
  const p = match.participant;
  const score = [p.kills, p.deaths, p.assists].map((v) => v ?? "—").join(" / ");
  const cs = p.totalMinionsKilled == null || p.neutralMinionsKilled == null ? null : p.totalMinionsKilled + p.neutralMinionsKilled;
  const result = p.win == null ? null : p.win ? "win" : "loss";
  const resultText = result === "win" ? "Vitória" : result === "loss" ? "Derrota" : "Sem resultado";
  const when = shortWhen(match.endedAt);
  return (
    <li className="panel-match result-bar" data-result={result ?? undefined}>
      <span className={`panel-match__result${result ? ` result-text--${result}` : ""}`}>{resultText}</span>
      <span className="panel-match__who">
        <ChampionAvatar participant={p} size={36} className="portrait" />
        <span className="panel-match__name">
          <strong>{championName(p)}</strong>
          <span>{queueLabel(queue)}</span>
          <span className="panel-match__meta" aria-hidden="true">
            <span className={result ? `result-text--${result}` : undefined}>{resultText}</span> · {when}
          </span>
        </span>
      </span>
      <span className="panel-match__when">
        <time dateTime={match.endedAt}>{when}</time>
        <span>Duração <span>{matchDuration(match.durationSeconds)}</span></span>
      </span>
      <span className="panel-match__score">
        <strong aria-label={`Abates, mortes e assistências: ${score}`}>{score}</strong>
        <span>K / D / A · <span>{cs == null ? "tropas indisponíveis" : count(cs, "tropa", "tropas")}</span></span>
      </span>
      <Link
        to={{ name: "historyMatch", matchId: match.matchId, participantId: p.participantId }}
        className="panel-match__action"
        aria-label={`Contexto da partida ${match.matchId}`}
      ><EyeIcon /><span>Contexto</span></Link>
    </li>
  );
}

export function Dashboard() {
  const [origin, setOrigin] = useState<"REAL" | "DEMO">("REAL");
  useDemoNotice(origin === "DEMO");
  const [windowSize, setWindowSize] = useState(20);
  const [queue, setQueue] = useState<DashboardQueueParam | "AUTO">("AUTO");
  const [retry, setRetry] = useState(0);
  const key = `${origin}:${windowSize}:${queue}:${retry}`;
  const [result, setResult] = useState<{ key: string; data: DashboardData } | null>(null);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api.getDashboard({ origin, window: windowSize, queue: queue === "AUTO" ? undefined : queue }, controller.signal).then((data) => {
      if (!controller.signal.aborted) setResult({ key, data });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted && !isAbortError(error)) setErrorKey(key);
    });
    return () => controller.abort();
  }, [origin, windowSize, queue, key]);
  const data = result?.key === key ? result.data : null;
  const failed = errorKey === key;
  const ready = data?.status === "READY" ? data : null;
  const champions = ready?.champions.slice(0, 3) ?? [];

  return (
    <>
      <header className="page-head panel-head">
        <div>
          <p className="page-head__context">{contextLine(data)}</p>
          <h2>Entenda a partida. Evolua a decisão.</h2>
          <p className="page-head__lead">Olhe o contexto, reconheça padrões e estude as compras, sem prometer uma resposta universal.</p>
        </div>
        <div className="panel-filters" role="group" aria-label="Recorte do Painel">
          <label>Fila<select value={queue} onChange={(event) => setQueue(event.target.value as DashboardQueueParam | "AUTO")}>
            <option value="AUTO">Automática</option><option value="RANKED_SOLO_5x5">Solo/Duo</option><option value="RANKED_FLEX_SR">Flex</option>
          </select></label>
          <label>Origem<select value={origin} onChange={(event) => setOrigin(event.target.value as "REAL" | "DEMO")}>
            <option value="REAL">Conta própria · real</option><option value="DEMO">Demonstração sintética</option>
          </select></label>
          <label>Partidas<select value={windowSize} onChange={(event) => setWindowSize(Number(event.target.value))}>
            {[10, 20, 50, 100].map((size) => <option key={size} value={size}>{size}</option>)}
          </select></label>
          {data ? <p className="panel-filters__hint">{queueSentence(data)}</p> : null}
        </div>
      </header>

      <div className="panel-grid">
        <div className="panel-main">
          <section className="surface panel-metrics" aria-labelledby="dashboard-heading">
            <h3 id="dashboard-heading" className="sr-only">Seu recorte de partidas</h3>
            {failed ? <div className="panel-metrics__state" role="alert"><p>Não foi possível carregar o recorte da conta.</p>
              <button type="button" className="button button--secondary" onClick={() => setRetry(retry + 1)}>Recarregar recorte</button></div>
              : !data ? <p className="panel-metrics__state" role="status" aria-busy="true">Carregando o recorte…</p> : <>
                {data.origin === "DEMO" ? <p className="panel-metrics__source">DEMO · participante 1 das partidas sintéticas; não representa seu desempenho.</p> : null}
                {data.status === "IDENTITY_UNAVAILABLE" ? <div className="panel-metrics__state">
                  <strong>Conta própria ainda sem vínculo observado</strong>
                  <p>Atualize Minha posição pelo comando administrativo para registrar o vínculo da conta. Depois, importe suas partidas. As partidas de terceiros continuam disponíveis para estudo no Histórico.</p>
                  <Link to="/minha-posicao" className="arrow-link">Consultar Minha posição</Link>
                </div> : data.status === "EMPTY" ? <div className="panel-metrics__state">
                  <strong>Nenhuma partida neste recorte</strong>
                  <p>Importe partidas concluídas {origin === "REAL" ? "da conta configurada" : "da demonstração"} para ver as métricas.</p>
                  <Link to="/importar" className="arrow-link">Importar ou abrir demonstração</Link>
                </div> : <div className="panel-metrics__row">
                  <div className="metric">
                    <p className="metric__label">Taxa de vitória</p>
                    <p className="metric__value">{data.winRate === null ? "—" : `${number(data.winRate * 100)}%`}</p>
                    <p className="metric__foot">{data.winRate === null ? "Indisponível: nenhuma partida com resultado. " : ""}{count(data.wins, "vitória", "vitórias")} em {count(data.winSamples, "partida", "partidas")} com resultado de {data.sampledMatches} no recorte</p>
                    {data.winRate === null ? null : <div className="metric__bar" aria-hidden="true"><span style={{ width: `${data.winRate * 100}%` }} /></div>}
                  </div>
                  <div className="metric">
                    <p className="metric__label">KDA médio</p>
                    <p className="metric__value">{data.kda === null ? "—" : number(data.kda)}</p>
                    <p className="metric__foot">(Abates + assistências) / mortes, mínimo 1 · {count(data.kdaSamples, "partida", "partidas")} com placar completo de {data.sampledMatches}</p>
                  </div>
                </div>}
                <div className="panel-metrics__secondary">
                  <p className="metric-secondary"><span>Decisões revistas</span> <strong>{data.progress.revealed}</strong> · de {count(data.progress.attempts, "tentativa", "tentativas")}, {count(data.progress.reflections, "com reflexão", "com reflexão")}</p>
                  {ready ? <p className="metric-secondary panel-metrics__champions">
                    <span>Campeões no recorte</span> <strong>{ready.distinctChampions}</strong>
                    <span className="panel-metrics__faces" aria-hidden="true">
                      {champions.map((champion) => <ChampionAvatar key={champion.championId} participant={champion} size={22} className="portrait" />)}
                    </span>
                  </p> : null}
                </div>
              </>}
          </section>

          {ready ? (
            <section className="panel-recent" aria-labelledby="own-recent-heading">
              <div className="section-head"><h3 id="own-recent-heading">Partidas recentes</h3></div>
              <div className="surface">
                <ul className="row-list">{ready.recentMatches.map((match) => <RecentRow key={match.matchId} match={match} queue={ready.queue} />)}</ul>
                <div className="panel-recent__foot">
                  <span>Mostrando as {ready.recentMatches.length} mais recentes de {count(ready.sampledMatches, "partida", "partidas")} do recorte</span>
                  <Link to="/historico" className="arrow-link">{ready.totalMatches === 1 ? "Ver a partida no Histórico" : `Ver as ${ready.totalMatches} partidas no Histórico`}</Link>
                </div>
              </div>
            </section>
          ) : <RecentMatches />}
        </div>

        <aside className="panel-side" aria-label="Posição e campeões">
          <MyRank compact />
          {champions.length ? (
            <section className="panel-champions" aria-labelledby="panel-champions-heading">
              <div className="section-head">
                <h3 id="panel-champions-heading">Campeões em foco</h3>
                <Link to="/campeoes" className="arrow-link">Todos os guias</Link>
              </div>
              <ul className="surface row-list">{champions.map((champion) => {
                const name = championName(champion);
                return (
                  <li key={champion.championId} className="panel-champion">
                    <ChampionAvatar participant={champion} size={36} className="portrait" />
                    <span className="panel-champion__text">
                      <strong>{name}</strong>
                      <span>{count(champion.matches, "partida", "partidas")} no recorte</span>
                    </span>
                    <Link to={`/campeoes?champion=${champion.championId}`} className="arrow-link" aria-label={`Ver guia de ${name}`}>Ver guia</Link>
                  </li>
                );
              })}</ul>
            </section>
          ) : null}
        </aside>
      </div>
    </>
  );
}
