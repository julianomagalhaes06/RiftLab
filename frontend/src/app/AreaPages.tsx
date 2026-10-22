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

export function PositionPage() {
  return (
    <div className="area-page">
      <div className="page-intro">
        <p className="page-intro__kicker">Minha posição</p>
        <h2>Classificação da conta própria</h2>
        <p>Observações das filas Solo/Duo e Flex da conta configurada localmente, com histórico e PDL separados por fila. Nas divisões até Diamante, a promoção ocorre ao alcançar 100 PDL; pontos excedentes podem seguir para a próxima divisão. Atualizações exigem o comando administrativo de refresh. A variação de PDL não é atribuída a uma partida importada.</p>
      </div>
      <MyRank />
    </div>
  );
}
