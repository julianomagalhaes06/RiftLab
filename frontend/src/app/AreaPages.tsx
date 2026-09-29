import { Link } from "../shared/router/Link";
import { MyRank } from "../features/ranked/MyRank";

export function PanelPage() {
  return (
    <div className="area-page">
      <div className="page-intro">
        <p className="page-intro__kicker">Revisão pós-partida</p>
        <h2>Entenda a partida.<br />Evolua a decisão.</h2>
        <p>O estudo começa no contexto de uma partida concluída. A posição da conta é exibida quando existe observação oficial.</p>
      </div>
      <div className="panel-layout">
        <section className="feature-surface" aria-labelledby="panel-study-heading">
          <p className="feature-surface__index">01 / estudo</p>
          <h3 id="panel-study-heading">Volte a uma decisão de compra</h3>
          <p>Consulte o Histórico, escolha uma partida e um participante, então abra uma decisão reconstruída.</p>
          <Link to="/historico" className="button">Abrir Histórico</Link>
        </section>
        <div className="panel-side">
          <section className="quiet-surface">
            <h3>Nova partida</h3>
            <p>Importe um ID de partida concluída ou percorra a demonstração sintética.</p>
            <Link to="/">Importar ou abrir demonstração</Link>
          </section>
          <MyRank compact />
        </div>
      </div>
    </div>
  );
}

export function ChampionsPage() {
  return (
    <div className="area-page">
      <div className="page-intro">
        <p className="page-intro__kicker">Campeões</p>
        <h2>Contexto por campeão</h2>
        <p>Esta área reunirá padrões observados quando houver amostra suficiente e decisões elegíveis.</p>
      </div>
      <section className="quiet-surface quiet-surface--wide">
        <h3>Sem análise de campeão disponível</h3>
        <p>O recorte real ainda não produziu decisões elegíveis. Nenhuma build ou recomendação é inferida dos exemplos sintéticos.</p>
        <Link to="/historico">Examinar partidas importadas</Link>
      </section>
    </div>
  );
}

export function PositionPage() {
  return (
    <div className="area-page">
      <div className="page-intro">
        <p className="page-intro__kicker">Minha posição</p>
        <h2>Classificação da conta própria</h2>
        <p>Observações da fila Solo/Duo da conta configurada localmente. Nas divisões até Diamante, a promoção ocorre ao alcançar 100 PDL; pontos excedentes podem seguir para a próxima divisão. Atualizações exigem o comando administrativo de refresh.</p>
      </div>
      <MyRank />
    </div>
  );
}
