import { MyRank } from "../features/ranked/MyRank";
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

/** Minha posição (CC-1204): card de elo grande à esquerda; observação, variação e regras do PDL à
 * direita. No celular, uma coluna nessa ordem. */
export function PositionPage() {
  return (
    <div className="area-page position-page">
      <header className="page-head">
        <p className="page-head__context">Minha posição</p>
        <h2>Classificação da conta própria</h2>
        <p className="page-head__lead">Solo/Duo e Flex da conta configurada, com observações e PDL separados por fila.</p>
      </header>
      <MyRank />
    </div>
  );
}
