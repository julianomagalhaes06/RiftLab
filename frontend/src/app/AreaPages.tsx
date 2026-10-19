import { Link } from "../shared/router/Link";
import { MyRank } from "../features/ranked/MyRank";
import { Dashboard } from "../features/dashboard/Dashboard";

export function PanelPage() {
  return (
    <div className="area-page panel-page">
      <div className="page-intro">
        <p className="page-intro__kicker">Revisão pós-partida</p>
        <h2>Entenda a partida. Evolua a decisão.</h2>
        <p>Abra o contexto de uma partida concluída e estude as decisões de compra, com seus limites de observação.</p>
      </div>
      {/* CC-905: a posição vem primeiro no DOM (topo no celular); no desktop fica à direita. */}
      <div className="panel-layout">
        <div className="panel-rank">
          <MyRank compact />
        </div>
        <div className="panel-main">
          <section className="study-entry" aria-labelledby="panel-study-heading">
            <div>
              <h3 id="panel-study-heading">Volte a uma decisão de compra</h3>
              <p>Escolha uma partida, um participante e uma decisão reconstruída.</p>
            </div>
            <Link to="/historico" className="button">Começar pelo Histórico</Link>
          </section>
          <Dashboard />
        </div>
        <section className="quiet-surface panel-import">
          <h3>Nova partida</h3>
          <p>Importe um ID de partida concluída ou percorra a demonstração sintética.</p>
          <Link to="/importar">Importar ou abrir demonstração</Link>
        </section>
      </div>
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
