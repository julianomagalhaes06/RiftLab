import { useEffect, useRef, useState } from "react";
import { api, type Backstage, type RiotCallRow } from "../../shared/api/client";
import { ApiError, isAbortError } from "../../shared/api/errors";
import { Link } from "../../shared/router/Link";
import "./backstage.css";

/**
 * Bastidores (só administradores): o que o RiftLab está fazendo agora. Chamadas à API da Riot
 * ao vivo, estado da chave, fila do worker, importações e sincronizações recentes e o tamanho
 * do acervo. Atualiza a cada 2 segundos; linhas novas piscam ao chegar.
 */

const POLL_MS = 2000;

const KIND_LABEL: Record<string, { api: string; what: string }> = {
  account: { api: "Account-V1", what: "conta pelo Riot ID" },
  "match-list": { api: "Match-V5", what: "lista de partidas" },
  match: { api: "Match-V5", what: "partida" },
  timeline: { api: "Match-V5", what: "linha do tempo" },
  "match-structural": { api: "Match-V5", what: "partida (verificação)" },
  league: { api: "League-V4", what: "elo" },
  summoner: { api: "Summoner-V4", what: "perfil" },
  spectator: { api: "Spectator-V5", what: "partida ao vivo" },
};

const OUTCOME: Record<string, { label: string; tone: "ok" | "bad" | "warn" | "muted" }> = {
  HTTP_200: { label: "OK", tone: "ok" },
  HTTP_AUTH: { label: "chave recusada", tone: "bad" },
  HTTP_NOT_FOUND: { label: "não encontrado", tone: "muted" },
  HTTP_429: { label: "limite da Riot", tone: "warn" },
  HTTP_5XX: { label: "Riot fora do ar", tone: "bad" },
  HTTP_UNEXPECTED: { label: "resposta inesperada", tone: "warn" },
  NETWORK_ERROR: { label: "sem rede", tone: "bad" },
};

const JOB_STATE: Record<string, string> = {
  QUEUED: "na fila",
  RUNNING: "em andamento",
  RETRY_WAIT: "aguardando nova tentativa",
  SUCCEEDED: "concluída",
  FAILED: "falhou",
};

const JOB_KIND: Record<string, string> = { IMPORT: "Importação", PREPARE: "Análise", PURGE: "Expurgo" };

const number = (value: number) => value.toLocaleString("pt-BR");

function time(value: string, seconds = true): string {
  return new Date(value).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: seconds ? "2-digit" : undefined });
}

function ago(value: string | null | undefined, now: number): string {
  if (!value) return "sem registro";
  const seconds = Math.max(0, Math.round((now - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `há ${seconds} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `há ${minutes} min`;
  return `há ${Math.round(minutes / 60)} h`;
}

function duration(start: string, end: string | null | undefined): string {
  if (!end) return "—";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  return ms < 1000 ? `${Math.max(0, ms)} ms` : `${(ms / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} s`;
}

function KeyCard({ data, now }: { data: Backstage; now: number }) {
  const status = data.riotKey.status;
  const text = status === "OK" ? "Aceita" : status === "REJECTED" ? "Recusada" : "Sem uso recente";
  const foot =
    status === "REJECTED"
      ? "A Riot recusou a chave. A de desenvolvimento expira em 24 h: renove e reinicie a API e o worker."
      : status === "OK"
        ? `Última resposta da Riot ${ago(data.riotKey.checkedAt, now)}.`
        : "Nenhuma resposta conclusiva da Riot nas últimas 24 h.";
  return (
    <article className={`backstage-card surface backstage-card--${status.toLowerCase()}`}>
      <p className="metric__label">Chave da API da Riot</p>
      <p className="backstage-card__value"><span className="backstage-dot" aria-hidden="true" />{text}</p>
      <p className="metric__foot">{foot}</p>
    </article>
  );
}

function CallsRow({ call, fresh }: { call: RiotCallRow; fresh: boolean }) {
  const kind = KIND_LABEL[call.kind] ?? { api: "Riot", what: call.kind };
  const outcome = OUTCOME[call.outcome] ?? { label: call.outcome, tone: "warn" as const };
  return (
    <tr className={fresh ? "backstage-calls__row backstage-calls__row--fresh" : "backstage-calls__row"}>
      <td className="backstage-mono">{time(call.at)}</td>
      <td>{call.process === "worker" ? "Worker" : call.process === "api" ? "API" : "Linha de comando"}</td>
      <td><strong>{kind.api}</strong> <span className="muted">{kind.what}</span></td>
      <td className="backstage-mono">{call.region}</td>
      <td>
        <span className={`backstage-status backstage-status--${outcome.tone}`}>
          {call.statusCode ?? "—"} {outcome.label}
        </span>
        {call.attempt > 1 ? <span className="muted"> · tentativa {call.attempt}</span> : null}
      </td>
      <td className="backstage-mono backstage-num">{number(call.durationMs)} ms</td>
    </tr>
  );
}

export function BackstagePage() {
  const [data, setData] = useState<Backstage | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "forbidden" | "error">("loading");
  const [now, setNow] = useState(() => Date.now());
  const seen = useRef<number | null>(null);
  const [freshAbove, setFreshAbove] = useState<number | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    const load = () => {
      api
        .getBackstage(controller.signal)
        .then((next) => {
          if (controller.signal.aborted) return;
          const newest = next.calls[0]?.id ?? null;
          // Linhas mais novas que a última leitura piscam; na primeira leitura, nenhuma.
          setFreshAbove(seen.current);
          seen.current = newest ?? seen.current;
          setData(next);
          setNow(Date.now());
          setState("ready");
          timer = window.setTimeout(load, POLL_MS);
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted || isAbortError(error)) return;
          if (error instanceof ApiError && error.status === 404) {
            setState("forbidden");
            return;
          }
          setState((current) => (current === "ready" ? current : "error"));
          timer = window.setTimeout(load, POLL_MS * 2);
        });
    };
    load();
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, []);

  if (state === "forbidden") {
    return (
      <section className="area-page" aria-labelledby="backstage-missing">
        <h2 id="backstage-missing">Página não encontrada</h2>
        <p className="muted">O endereço não corresponde a nenhuma tela do RiftLab.</p>
        <p><Link to="/">Voltar ao início</Link></p>
      </section>
    );
  }
  if (!data) {
    return (
      <section className="area-page" aria-busy={state === "loading"}>
        <p className="muted" role="status">{state === "error" ? "Não foi possível abrir os Bastidores agora." : "Abrindo os Bastidores…"}</p>
      </section>
    );
  }

  const hour = data.lastHour;
  const rate = hour.calls ? Math.round((hour.succeeded / hour.calls) * 100) : null;
  const queue = Object.fromEntries(data.worker.queue.map((entry) => [entry.kind, entry.states]));
  const importing = queue.IMPORT ?? {};
  const preparing = queue.PREPARE ?? {};
  const waiting = (importing.QUEUED ?? 0) + (importing.RETRY_WAIT ?? 0);
  const counters = data.counters;

  return (
    <div className="backstage">
      <header className="page-head">
        <p className="page-head__context">Bastidores · só administradores</p>
        <h2>O que o RiftLab está fazendo agora</h2>
        <p className="page-head__lead">
          Cada consulta à API oficial da Riot aparece aqui no momento em que acontece, com o tempo de resposta.
          Atualiza a cada 2 segundos.
        </p>
      </header>

      <section className="backstage-cards" aria-label="Resumo">
        <KeyCard data={data} now={now} />
        <article className="backstage-card surface">
          <p className="metric__label">Chamadas à Riot na última hora</p>
          <p className="backstage-card__value">{number(hour.calls)}</p>
          <p className="metric__foot">
            {rate === null ? "Nenhuma chamada na última hora." : `${rate}% com sucesso · média de ${number(hour.averageMs ?? 0)} ms`}
            {hour.rateLimited ? ` · ${number(hour.rateLimited)} no limite da Riot` : ""}
          </p>
        </article>
        <article className="backstage-card surface">
          <p className="metric__label">Worker</p>
          <p className="backstage-card__value">{number(importing.RUNNING ?? 0)} <span className="backstage-card__unit">baixando agora</span></p>
          <p className="metric__foot">
            {number(waiting)} na fila · {number((preparing.QUEUED ?? 0) + (preparing.RUNNING ?? 0))} análises pendentes · última chamada à Riot {ago(data.worker.lastRiotCallAt, now)}
          </p>
        </article>
        <article className="backstage-card surface">
          <p className="metric__label">Últimas 24 h</p>
          <p className="backstage-card__value">{number(data.worker.succeededToday)} <span className="backstage-card__unit">tarefas concluídas</span></p>
          <p className="metric__foot">{number(data.worker.failedToday)} com falha · importações, análises e expurgos</p>
        </article>
      </section>

      <section className="backstage-section surface" aria-labelledby="backstage-calls">
        <div className="section-head">
          <h3 id="backstage-calls"><span className="backstage-live" aria-hidden="true" />Chamadas à API da Riot, ao vivo</h3>
          <span className="muted">últimas {data.calls.length}</span>
        </div>
        {hour.byKind.length ? (
          <ul className="backstage-kinds" aria-label="Consultas na última hora">
            {hour.byKind.map((entry) => (
              <li key={entry.kind} className="chip">
                {(KIND_LABEL[entry.kind]?.what ?? entry.kind)}: {number(entry.calls)} · {number(entry.averageMs)} ms
              </li>
            ))}
          </ul>
        ) : null}
        {data.calls.length ? (
          <div className="backstage-table-wrap">
            <table className="backstage-table backstage-calls">
              <thead>
                <tr><th scope="col">Hora</th><th scope="col">Quem chamou</th><th scope="col">Consulta</th><th scope="col">Região</th><th scope="col">Resposta</th><th scope="col" className="backstage-num">Tempo</th></tr>
              </thead>
              <tbody>
                {data.calls.map((call) => (
                  <CallsRow key={call.id} call={call} fresh={freshAbove !== null && call.id > freshAbove} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">Nenhuma chamada registrada nas últimas 24 h. Pesquise um jogador para ver as consultas chegando.</p>
        )}
      </section>

      <div className="backstage-columns">
        <section className="backstage-section surface" aria-labelledby="backstage-jobs">
          <div className="section-head"><h3 id="backstage-jobs">Fila do worker</h3></div>
          {data.jobs.length ? (
            <div className="backstage-table-wrap">
              <table className="backstage-table">
                <thead><tr><th scope="col">Tarefa</th><th scope="col">Partida</th><th scope="col">Estado</th><th scope="col" className="backstage-num">Duração</th></tr></thead>
                <tbody>
                  {data.jobs.map((job) => (
                    <tr key={`${job.matchId}-${job.kind}-${job.createdAt}`}>
                      <td>{JOB_KIND[job.kind] ?? job.kind}{job.fromSync ? <span className="muted"> · da busca</span> : null}</td>
                      <td className="backstage-mono">{job.matchId}</td>
                      <td>
                        <span className={`backstage-status backstage-status--${job.state === "SUCCEEDED" ? "ok" : job.state === "FAILED" ? "bad" : "warn"}`}>{JOB_STATE[job.state] ?? job.state}</span>
                        {job.errorCode ? <span className="muted"> · {job.errorCode}</span> : null}
                      </td>
                      <td className="backstage-mono backstage-num">{job.state === "SUCCEEDED" || job.state === "FAILED" ? duration(job.createdAt, job.updatedAt) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p className="muted">Nenhuma tarefa registrada.</p>}
        </section>

        <section className="backstage-section surface" aria-labelledby="backstage-syncs">
          <div className="section-head"><h3 id="backstage-syncs">Buscas de histórico</h3></div>
          {data.syncs.length ? (
            <div className="backstage-table-wrap">
              <table className="backstage-table">
                <thead><tr><th scope="col">Pedida às</th><th scope="col">Resultado</th><th scope="col" className="backstage-num">Na Riot</th></tr></thead>
                <tbody>
                  {data.syncs.map((run) => {
                    const steps = run.steps ?? {};
                    const riotMs = Object.values(steps).reduce((sum, value) => sum + value, 0);
                    return (
                      <tr key={run.requestedAt}>
                        <td className="backstage-mono">{time(run.requestedAt)}</td>
                        <td>
                          {run.state === "SUCCEEDED"
                            ? `${number(run.listed)} listadas · ${number(run.newEnqueued)} novas · ${number(run.alreadyKnown)} já no RiftLab`
                            : run.state === "FAILED"
                              ? <span className="backstage-status backstage-status--bad">falhou · {run.errorCode}</span>
                              : JOB_STATE[run.state] ?? run.state}
                        </td>
                        <td className="backstage-mono backstage-num">{run.steps ? `${number(riotMs)} ms` : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : <p className="muted">Nenhuma busca registrada.</p>}
        </section>
      </div>

      <section className="backstage-section surface" aria-labelledby="backstage-store">
        <div className="section-head"><h3 id="backstage-store">Acervo no banco</h3><span className="muted">só partidas reais da Riot</span></div>
        <dl className="backstage-counters">
          <div><dt>Partidas</dt><dd>{number(counters.matches)}</dd></div>
          <div><dt>Participações</dt><dd>{number(counters.participants)}</dd></div>
          <div><dt>Eventos da linha do tempo</dt><dd>{number(counters.timelineEvents)}</dd></div>
          <div><dt>Decisões de compra analisadas</dt><dd>{number(counters.decisions)}</dd></div>
          <div><dt>Tentativas de estudo</dt><dd>{number(counters.studyAttempts)}</dd></div>
          <div><dt>Jogadores pesquisados</dt><dd>{number(counters.playersSearched)}</dd></div>
          <div><dt>Contas no RiftLab</dt><dd>{number(counters.users)}</dd></div>
          <div><dt>Contas Riot vinculadas</dt><dd>{number(counters.linkedAccounts)}</dd></div>
        </dl>
      </section>
    </div>
  );
}
