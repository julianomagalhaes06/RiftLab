import type { HistorySyncStatus } from "../../shared/api/client";
import { active, clock } from "./syncMessage";

/**
 * Etapas visíveis da atualização com a Riot: cada consulta (conta, lista de partidas, elo) com
 * o tempo que levou, o download das partidas novas e o que foi lido delas. Mostra que os dados
 * chegam da API oficial na hora, com números reais desta execução.
 */

type StepState = "waiting" | "running" | "done" | "failed" | "warning";

interface Step {
  key: string;
  title: string;
  source: string;
  state: StepState;
  detail?: string;
  progress?: { done: number; total: number };
}

const number = (value: number) => value.toLocaleString("pt-BR");
const ms = (value: number | null | undefined) => (value == null ? undefined : `${number(value)} ms`);
const plural = (value: number, one: string, many: string) => `${number(value)} ${value === 1 ? one : many}`;

function riotSteps(status: HistorySyncStatus): Step[] {
  const steps: NonNullable<HistorySyncStatus["steps"]> = status.steps ?? {};
  const running = active(status);
  const failed = status.state === "FAILED";
  // Numa falha, a primeira etapa sem duração é a que não terminou.
  const order = [steps.accountMs, steps.listMs, steps.leagueMs];
  const firstMissing = order.findIndex((value) => value == null);
  const state = (index: number, value: number | null | undefined): StepState => {
    if (value != null) return "done";
    if (running) return index === Math.max(0, firstMissing) && status.state === "RUNNING" ? "running" : "waiting";
    if (failed) return index === firstMissing ? "failed" : "waiting";
    return "warning"; // terminou sem esta etapa (só o elo pode faltar sem derrubar a execução)
  };
  return [
    {
      key: "account",
      title: "Conta encontrada na Riot",
      source: "Account-V1",
      state: state(0, steps.accountMs),
      detail: ms(steps.accountMs),
    },
    {
      key: "list",
      title: "Partidas ranqueadas listadas",
      source: "Match-V5",
      state: state(1, steps.listMs),
      detail: steps.listMs == null ? undefined : `${plural(status.listed ?? 0, "partida", "partidas")} · ${ms(steps.listMs)}`,
    },
    {
      key: "league",
      title: steps.leagueMs == null && !running && !failed ? "Elo indisponível agora" : "Elo consultado",
      source: "League-V4",
      state: state(2, steps.leagueMs),
      detail: ms(steps.leagueMs),
    },
  ];
}

function downloadSteps(status: HistorySyncStatus): Step[] {
  if (status.state !== "SUCCEEDED") return [];
  const found = status.newEnqueued ?? 0;
  const known = status.alreadyKnown ?? 0;
  if (!found) {
    return [
      {
        key: "download",
        title: "Nenhuma partida nova para baixar",
        source: "RiftLab",
        state: "done",
        detail: known ? `${plural(known, "partida listada já estava", "partidas listadas já estavam")} no RiftLab` : undefined,
      },
    ];
  }
  const imports = status.imports;
  const evidence = status.evidence;
  const total = imports?.total ?? found;
  const succeeded = imports?.succeeded ?? 0;
  const failedImports = imports?.failed ?? 0;
  const pending = imports ? imports.pending : total;
  const downloading = pending > 0;
  const details = [
    known ? `${number(known)} já no RiftLab` : null,
    failedImports ? `${number(failedImports)} com falha` : null,
  ].filter(Boolean).join(" · ");
  return [
    {
      key: "download",
      title: downloading ? "Baixando partidas e linhas do tempo" : "Partidas novas baixadas",
      source: "Match-V5",
      state: downloading ? "running" : failedImports && !succeeded ? "failed" : "done",
      detail: `${number(succeeded)} de ${plural(total, "partida", "partidas")}${details ? ` · ${details}` : ""}`,
      progress: { done: succeeded + failedImports, total },
    },
    {
      key: "evidence",
      title: "Linha do tempo lida, minuto a minuto",
      source: "RiftLab",
      state: downloading ? (evidence?.timelineEvents ? "running" : "waiting") : evidence?.timelineEvents ? "done" : "warning",
      detail: evidence
        ? [
            plural(evidence.timelineEvents, "evento", "eventos"),
            plural(evidence.itemPurchases, "compra de item", "compras de itens"),
            plural(evidence.participants, "participante", "participantes"),
          ].join(" · ")
        : undefined,
    },
  ];
}

function Mark({ state }: { state: StepState }) {
  if (state === "running") return <span className="sync-step__mark sync-step__mark--running" aria-hidden="true" />;
  const symbol = state === "done" ? "✓" : state === "failed" ? "✕" : state === "warning" ? "!" : "";
  return <span className={`sync-step__mark sync-step__mark--${state}`} aria-hidden="true">{symbol}</span>;
}

const STATE_LABEL: Record<StepState, string> = {
  waiting: "aguardando",
  running: "em andamento",
  done: "concluída",
  failed: "falhou",
  warning: "indisponível",
};

export function SyncPipeline({ status }: { status: HistorySyncStatus | null }) {
  if (!status || status.state === "IDLE" || status.state === "UNAVAILABLE") return null;
  // Execução anterior às etapas registradas (sem durações) não tem o que mostrar.
  if (!active(status) && !status.steps) return null;
  const steps = [...riotSteps(status), ...downloadSteps(status)];
  const when = clock(status.finishedAt);
  const heading = active(status)
    ? "Buscando agora na API oficial da Riot"
    : status.reason === "COOLDOWN" && when
      ? `Última busca na API oficial da Riot, às ${when}`
      : `Busca na API oficial da Riot${when ? `, às ${when}` : ""}`;
  return (
    <details className="sync-pipeline surface" open>
      <summary className="sync-pipeline__summary">
        <span className="sync-pipeline__kicker">Como esses dados chegaram</span>
        <span className="sync-pipeline__heading">{heading}</span>
      </summary>
      <ol className="sync-pipeline__steps">
        {steps.map((step) => (
          <li key={step.key} className={`sync-step sync-step--${step.state}`}>
            <Mark state={step.state} />
            <div className="sync-step__body">
              <p className="sync-step__title">
                {step.title}
                <span className="sr-only">: {STATE_LABEL[step.state]}</span>
              </p>
              {step.progress ? (
                <progress
                  className="sync-step__progress"
                  max={step.progress.total}
                  value={step.progress.done}
                  aria-label={`${step.title}: ${step.progress.done} de ${step.progress.total}`}
                />
              ) : null}
              {step.detail ? <p className="sync-step__detail">{step.detail}</p> : null}
            </div>
            <span className="sync-step__source">{step.source}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}
