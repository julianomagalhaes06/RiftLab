import { useEffect, useRef, type ReactNode } from "react";
import { DecisionDetailPage } from "../features/matches/DecisionDetailPage";
import { HistoryPage } from "../features/history/HistoryPage";
import { EntryPage } from "../features/imports/EntryPage";
import { ImportProgressPage } from "../features/imports/ImportProgressPage";
import { MatchPage } from "../features/matches/MatchPage";
import { ReviewPrototype } from "../features/review/ReviewPrototype";
import "../features/review/review.css";
import { useRouter } from "../shared/router/context";
import { Link } from "../shared/router/Link";
import { RouterProvider } from "../shared/router/RouterProvider";
import type { Route } from "../shared/router/routes";
import { HealthStatus } from "./HealthStatus";
import { PanelPage, PositionPage } from "./AreaPages";
import { ChampionsPage } from "../features/champions/ChampionsPage";
import { MainNav } from "./MainNav";
import "./App.css";
import "../design/concept-c.css";

const TITLES: Record<Route["name"], string> = {
  home: "RiftLab",
  panel: "Painel — RiftLab",
  champions: "Campeões — RiftLab",
  position: "Minha posição — RiftLab",
  history: "Histórico — RiftLab",
  historyMatch: "Contexto da partida — RiftLab",
  import: "Importação — RiftLab",
  match: "Partida — RiftLab",
  decision: "Decisão — RiftLab",
  prototype: "Protótipo (dados ilustrativos) — RiftLab",
  notFound: "Página não encontrada — RiftLab",
};

function renderPage(route: Route): ReactNode {
  switch (route.name) {
    case "home":
      return (
        <>
          <HealthStatus />
          <EntryPage />
        </>
      );
    case "panel":
      return <PanelPage />;
    case "champions":
      return <ChampionsPage />;
    case "position":
      return <PositionPage />;
    case "history":
      return <HistoryPage />;
    case "historyMatch":
      return <MatchPage key={route.matchId} matchId={route.matchId} participantId={route.participantId} fromHistory />;
    case "import":
      return <ImportProgressPage key={route.jobId} jobId={route.jobId} />;
    case "match":
      return <MatchPage key={route.matchId} matchId={route.matchId} participantId={route.participantId} />;
    case "decision":
      return <DecisionDetailPage key={route.decisionId} decisionId={route.decisionId} />;
    case "notFound":
      return (
        <section className="section" aria-labelledby="not-found-heading">
          <h2 id="not-found-heading">Página não encontrada</h2>
          <p className="muted">O endereço não corresponde a nenhuma tela do RiftLab.</p>
          <p>
            <Link to="/">Voltar ao início</Link>
          </p>
        </section>
      );
    case "prototype":
      return null; // renderizado fora do layout principal
  }
}

function AppShell() {
  const { route, navigate, href } = useRouter();
  const mainRef = useRef<HTMLElement>(null);
  // Só o CAMINHO conta: trocar de participante (?participant=N) não devolve o foco ao
  // topo da página, senão quem navega por teclado perderia o lugar a cada seleção.
  const pathKey = href.split("?")[0];
  const firstRender = useRef(true);

  useEffect(() => {
    document.title = TITLES[route.name];
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    // Navegação por link: leva o foco ao conteúdo novo (teclado/leitor de tela).
    mainRef.current?.focus();
  }, [pathKey, route.name]);

  if (route.name === "prototype") {
    return <ReviewPrototype onExit={() => navigate("/")} />;
  }

  return (
    <>
      <a className="skip-link" href="#conteudo">
        Ir para o conteúdo
      </a>
      <div className="app">
        <header>
          <div className="app__topbar">
            <h1>
              <Link to="/painel" className="app__brand">
                <span className="app__mark" aria-hidden="true">R</span>RiftLab
              </Link>
            </h1>
            <MainNav route={route} />
            <Link to="/" className="app__import-link">Importar partida</Link>
          </div>
          <div className="app__source-banner" role="note">
            Partidas DEMO são sintéticas e identificadas. A posição da conta local vem de observações da Riot e não atribui resultados às partidas importadas.
          </div>
        </header>
        <main id="conteudo" ref={mainRef} tabIndex={-1} className="app__main">
          {renderPage(route)}
        </main>
        <footer className="app__footer">
          <p>
            Revisão de partidas concluídas; o RiftLab não acompanha partidas ao vivo. Nomes e
            retratos de campeões: Data Dragon, da Riot Games. Emblemas de elo: pacote Ranked Emblems da Riot Games.
          </p>
          <p lang="en">
            RiftLab isn&apos;t endorsed by Riot Games and doesn&apos;t reflect the views or
            opinions of Riot Games or anyone officially involved in producing or managing Riot
            Games properties. Riot Games, and all associated properties are trademarks or
            registered trademarks of Riot Games, Inc.
          </p>
        </footer>
      </div>
    </>
  );
}

export default function App() {
  return (
    <RouterProvider>
      <AppShell />
    </RouterProvider>
  );
}
