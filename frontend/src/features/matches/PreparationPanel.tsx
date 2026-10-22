import type { MatchPreparation } from "../../shared/api/client";
import { messageForCode } from "../../shared/api/messages";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";

function formatClock(iso: string | null | undefined): string | null {
  if (!iso) {
    return null;
  }
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleTimeString("pt-BR");
}

interface PreparationPanelProps {
  preparation: MatchPreparation;
  busy: boolean;
  onPrepare: () => void;
  /** Erro da última chamada POST de preparação (já traduzido pela tabela única). */
  requestError?: unknown;
}

/**
 * Estado da preparação (catálogo → reconstrução → extração). A região `role="status"`
 * anuncia mudanças; FAILED e NOT_REQUESTED oferecem a ação para (re)preparar.
 */
export function PreparationPanel({ preparation, busy, onPrepare, requestError }: PreparationPanelProps) {
  const retryAt = formatClock(preparation.retryAt);
  let status: string;
  switch (preparation.status) {
    case "PENDING":
      status = "Preparando a partida: aguardando na fila…";
      break;
    case "RUNNING":
      status = "Preparando a partida: reconstruindo o inventário e extraindo as decisões…";
      break;
    case "READY":
      status = "Partida pronta: escolha um participante no placar para ver as decisões.";
      break;
    case "FAILED":
      status = "A preparação da partida falhou.";
      break;
    case "NOT_REQUESTED":
      status = "Esta partida ainda não foi preparada.";
      break;
  }

  if (preparation.status === "READY" && !requestError) {
    // Pronta: só o anúncio para leitor de tela; o placar já é a tela.
    return <p className="sr-only" role="status" aria-live="polite">{status}</p>;
  }
  return (
    <section className="surface match-panel" aria-labelledby="preparation-heading">
      <h3 id="preparation-heading">Preparação</h3>
      <div className="progress" role="status" aria-live="polite">
        <p className={`progress__state progress__state--${preparation.status.toLowerCase()}`}>
          {status}
        </p>
        {preparation.status === "PENDING" && preparation.errorCode ? (
          <p className="muted">
            A tentativa anterior falhou: {messageForCode(preparation.errorCode)}
            {retryAt ? ` Nova tentativa por volta de ${retryAt}.` : ""}
          </p>
        ) : null}
      </div>
      {preparation.status === "FAILED" ? (
        <ErrorNotice code={preparation.errorCode} title="Não foi possível preparar a partida" />
      ) : null}
      {requestError ? <ErrorNotice error={requestError} /> : null}
      {preparation.status === "FAILED" || preparation.status === "NOT_REQUESTED" ? (
        <button type="button" className="button" onClick={onPrepare} disabled={busy}>
          {busy
            ? "Enviando…"
            : preparation.status === "FAILED"
              ? "Tentar preparar novamente"
              : "Preparar partida"}
        </button>
      ) : null}
    </section>
  );
}
