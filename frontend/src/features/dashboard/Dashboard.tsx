import { useEffect, useState } from "react";
import { api, type Dashboard as DashboardData } from "../../shared/api/client";
import { isAbortError } from "../../shared/api/errors";
import { Link } from "../../shared/router/Link";
import { ChampionBadge } from "../matches/ChampionBadge";
import { RecentMatches } from "../history/RecentMatches";
import { matchDate, matchDuration } from "../history/presentation";
import "./dashboard.css";

const number = (value: number) => value.toLocaleString("pt-BR", { maximumFractionDigits: 2 });

export function Dashboard() {
  const [origin, setOrigin] = useState<"REAL" | "DEMO">("REAL");
  const [windowSize, setWindowSize] = useState(20);
  const [retry, setRetry] = useState(0);
  const key = `${origin}:${windowSize}:${retry}`;
  const [result, setResult] = useState<{ key: string; data: DashboardData } | null>(null);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api.getDashboard({ origin, window: windowSize }, controller.signal).then((data) => {
      if (!controller.signal.aborted) setResult({ key, data });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted && !isAbortError(error)) setErrorKey(key);
    });
    return () => controller.abort();
  }, [origin, windowSize, key]);
  const data = result?.key === key ? result.data : null;
  const failed = errorKey === key;
  return <>
    <section className="dashboard" aria-labelledby="dashboard-heading">
      <div className="dashboard__heading">
        <div><h3 id="dashboard-heading">Seu recorte de partidas</h3>
          <p>Estatísticas descritivas da fila Solo/Duo importada.</p></div>
        <div className="dashboard__filters">
          <label>Origem<select value={origin} onChange={(event) => setOrigin(event.target.value as "REAL" | "DEMO")}>
            <option value="REAL">Conta própria · real</option><option value="DEMO">Demonstração sintética</option>
          </select></label>
          <label>Últimas partidas<select value={windowSize} onChange={(event) => setWindowSize(Number(event.target.value))}>
            {[10, 20, 50, 100].map((size) => <option key={size} value={size}>{size}</option>)}
          </select></label>
        </div>
      </div>
      {failed ? <div role="alert"><p>Não foi possível carregar o recorte da conta.</p>
        <button type="button" onClick={() => setRetry(retry + 1)}>Recarregar recorte</button></div>
        : !data ? <p role="status" aria-busy="true">Carregando o recorte…</p> : <>
          <p className="dashboard__source">{data.origin === "DEMO"
            ? "DEMO · participante 1 das partidas sintéticas; não representa seu desempenho."
            : "REAL · somente partidas vinculadas à conta configurada."}</p>
          {data.status === "IDENTITY_UNAVAILABLE" ? <div className="dashboard__empty">
            <strong>Conta própria ainda sem vínculo observado</strong>
            <p>Atualize Minha posição pelo comando administrativo para registrar o vínculo da conta. Depois, importe suas partidas. As partidas de terceiros continuam disponíveis para estudo no Histórico.</p>
            <Link to="/minha-posicao">Consultar Minha posição</Link>
          </div> : data.status === "EMPTY" ? <div className="dashboard__empty">
            <strong>Nenhuma partida neste recorte</strong>
            <p>Importe partidas concluídas {origin === "REAL" ? "da conta configurada" : "da demonstração"} para ver as métricas.</p>
            <Link to="/">Importar ou abrir demonstração</Link>
          </div> : <>
            <p>{data.sampledMatches} de até {data.window} partidas, ordenadas pelo término. {data.totalMatches} disponíveis da conta nesta origem.</p>
            <dl className="dashboard__metrics">
              <div><dt>Taxa de vitória</dt><dd>{data.winRate === null ? "Indisponível" : `${number(data.winRate * 100)}%`}<small>{data.wins} vitórias em {data.winSamples} partidas com resultado de {data.sampledMatches} no recorte</small></dd></div>
              <div><dt>KDA do recorte</dt><dd>{data.kda === null ? "Indisponível" : number(data.kda)}<small>{data.kdaSamples} partidas com placar completo de {data.sampledMatches}; (abates + assistências) ÷ mortes, mínimo 1</small></dd></div>
              <div><dt>Campeões no recorte</dt><dd>{data.distinctChampions}<small>Distintos nas {data.sampledMatches} partidas</small></dd></div>
            </dl>
            <div className="dashboard__champions"><h4>Campeões em foco</h4>
              <ul>{data.champions.map((champion) => <li key={champion.championId}>
                <ChampionBadge participant={champion} /><span>{champion.matches} de {data.sampledMatches} partidas</span>
                <Link to={`/campeoes?champion=${champion.championId}`}>Explorar campeão</Link>
              </li>)}</ul>
            </div>
            <section className="dashboard__recent" aria-labelledby="own-recent-heading">
              <h4 id="own-recent-heading">Suas partidas recentes</h4>
              <ul>{data.recentMatches.map((match) => {
                const p = match.participant;
                const score = [p.kills, p.deaths, p.assists].map((v) => v ?? "—").join(" / ");
                const cs = p.totalMinionsKilled == null || p.neutralMinionsKilled == null ? null : p.totalMinionsKilled + p.neutralMinionsKilled;
                return <li key={match.matchId}>
                  <div><ChampionBadge participant={p} /><strong>{p.win == null ? "Resultado indisponível" : p.win ? "Vitória" : "Derrota"}</strong></div>
                  <div><span>Abates / mortes / assistências: {score}</span><span>Tropas: {cs ?? "indisponíveis"} · duração {matchDuration(match.durationSeconds)}</span>
                    <time dateTime={match.endedAt}>Término: {matchDate(match.endedAt)}</time></div>
                  <Link to={{ name: "historyMatch", matchId: match.matchId, participantId: p.participantId }} aria-label={`Contexto da partida ${match.matchId}`}>Contexto</Link>
                </li>;
              })}</ul>
              <p>Mostrando {data.recentMatches.length} de {data.sampledMatches} partidas do recorte. <Link to="/historico">Ver as {data.totalMatches} partidas da conta no Histórico geral</Link></p>
              <small>O Histórico reúne todas as origens importadas. Tropas = tropas de rota + monstros neutros.</small>
            </section>
          </>}
          <section className="dashboard__progress" aria-labelledby="dashboard-progress-heading">
            <h4 id="dashboard-progress-heading">Seu estudo nesta instalação</h4>
            <p>{data.origin} · todas as datas, incluindo estudo de terceiros e exercícios sem pistas. Independente da janela de desempenho.</p>
            <dl className="dashboard__metrics">
              <div><dt>Decisões revistas</dt><dd>{data.progress.revealed}<small>Tentativas reveladas de {data.progress.attempts} iniciadas</small></dd></div>
              <div><dt>Reflexões registradas</dt><dd>{data.progress.reflections}<small>Sobre as tentativas desta origem</small></dd></div>
            </dl>
            <p>{data.progress.matches} partidas com tentativas iniciadas. Contagens de atividade; não medem aprendizagem ou evolução de elo.</p>
          </section>
        </>}
    </section>
    {(!data || data.status !== "READY") && <RecentMatches />}
  </>;
}
