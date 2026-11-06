import type { SyncPending } from "./HistorySync";

/**
 * Espera da atualização no topo da lista, como nos sites de estatísticas: em vez de as
 * partidas aparecerem uma a uma, a caixa avisa que estão chegando e a lista é trocada de
 * uma vez quando todas terminam de importar.
 */
export function SyncPendingBox({ pending }: { pending: SyncPending }) {
  const text = pending.expected
    ? `Trazendo ${pending.expected === 1 ? "1 partida nova" : `${pending.expected} partidas novas`} da Riot…`
    : "Procurando partidas novas na Riot…";
  return (
    <div className="sync-pending surface" role="status" aria-live="polite" aria-busy="true">
      <span className="sync-pending__spinner" aria-hidden="true" />
      <span>{text}</span>
    </div>
  );
}
