import { Dashboard } from "../features/dashboard/Dashboard";

/** Painel (CC-1203): cabeçalho, métricas, partidas recentes, card de elo e campeões em foco, como
 * `panel_desktop` do protótipo; no celular, métricas → partidas → elo → campeões. */
export function PanelPage() {
  return (
    <div className="area-page panel-page">
      <Dashboard />
    </div>
  );
}
