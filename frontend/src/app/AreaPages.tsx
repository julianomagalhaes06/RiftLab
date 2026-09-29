import { Link } from "../shared/router/Link";

export function PanelPage() {
  return (
    <div className="area-page">
      <div className="page-intro">
        <p className="page-intro__kicker">Revisão pós-partida</p>
        <h2>Entenda a partida.<br />Evolua a decisão.</h2>
        <p>O estudo começa no contexto de uma partida concluída. Os indicadores pessoais ainda não estão disponíveis.</p>
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
          <section className="quiet-surface">
            <h3>Indicadores pessoais</h3>
            <p>Taxa de vitória, KDA e partidas da sua conta dependem de um vínculo verificável. Não há valores para mostrar nesta etapa.</p>
          </section>
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
        <p>O RiftLab ainda não vinculou uma conta própria nem consultou o elo oficial dela.</p>
      </div>
      <section className="quiet-surface quiet-surface--wide">
        <h3>Classificação não disponível</h3>
        <p>Quando essa conexão existir, esta área mostrará apenas o elo da conta vinculada. Não há ranking de outros jogadores.</p>
        <Link to="/historico">Ir para o Histórico</Link>
      </section>
    </div>
  );
}
