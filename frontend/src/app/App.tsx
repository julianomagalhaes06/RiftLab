import "../design/tokens.css";
import "../design/arena.css";
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
import { DemoNoticeProvider } from "./DemoNotice";
import { ContextStrip } from "./ContextStrip";
import { loginPath, type Route } from "../shared/router/routes";
import { PanelPage, PositionPage } from "./AreaPages";
import { ChampionsPage } from "../features/champions/ChampionsPage";
import { MainNav } from "./MainNav";
import { PlayerPage } from "../features/players/PlayerPage";
import { LivePage } from "../features/live/LivePage";
import { PlayerSearch } from "../features/players/PlayerSearch";
import { ProfileChip } from "../features/players/ProfileChip";
import { AuthProvider } from "../features/auth/AuthProvider";
import { useAuth } from "../features/auth/authContext";
import { AuthScreen } from "../features/auth/AuthScreen";
import { UserMenu } from "../features/auth/UserMenu";
import { AccountPage } from "../features/auth/AccountPage";
import { ThemeToggle } from "../shared/theme/ThemeToggle";
import { HomePage } from "../features/home/HomePage";
import "./App.css";
import "../design/concept-c.css";

const TITLES: Record<Route["name"], string> = {
  home: "RiftLab — revisão de partidas de League of Legends",
  login: "Entrar — RiftLab",
  register: "Criar conta — RiftLab",
  forgot: "Redefinir senha — RiftLab",
  importEntry: "Importar partida — RiftLab",
  panel: "Painel — RiftLab",
  champions: "Campeões — RiftLab",
  position: "Minha posição — RiftLab",
  account: "Minha conta — RiftLab",
  history: "Histórico — RiftLab",
  historyMatch: "Contexto da partida — RiftLab",
  import: "Importação — RiftLab",
  match: "Partida — RiftLab",
  decision: "Decisão — RiftLab",
  player: "Jogador — RiftLab",
  live: "Partida ao vivo — RiftLab",
  prototype: "Protótipo (dados ilustrativos) — RiftLab",
  notFound: "Página não encontrada — RiftLab",
};

// Áreas da conta própria: sem sessão, vão direto para entrar ou criar conta (e voltam depois).
const ACCOUNT_AREAS: ReadonlySet<Route["name"]> = new Set(["panel", "position", "history", "account", "importEntry", "import"]);

function renderPage(route: Route): ReactNode {
  switch (route.name) {
    case "home":
      return <HomePage />;
    case "login":
    case "register":
    case "forgot":
      return null; // renderizadas fora do layout principal
    case "importEntry":
      return <EntryPage />;
    case "panel":
      return <PanelPage />;
    case "champions":
      return <ChampionsPage />;
    case "position":
      return <PositionPage />;
    case "account":
      return <AccountPage />;
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
    case "player":
      return (
        <PlayerPage
          key={`${route.gameName}#${route.tagLine}`}
          gameName={route.gameName}
          tagLine={route.tagLine}
          fromMatch={route.fromMatch ?? null}
          previousRiotId={route.previousRiotId ?? null}
        />
      );
    case "live":
      return <LivePage key={`${route.gameName}#${route.tagLine}`} gameName={route.gameName} tagLine={route.tagLine} />;
    case "notFound":
      return (
        <section className="area-page" aria-labelledby="not-found-heading">
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
  const { checked, user } = useAuth();
  const { route, navigate } = useRouter();
  const authRoute = route.name === "login" || route.name === "register" || route.name === "forgot" ? route : null;
  // Já com sessão, /entrar e /criar-conta levam direto ao destino.
  useEffect(() => {
    if (checked && user && authRoute) navigate(authRoute.next ?? "/painel", { replace: true });
  }, [checked, user, authRoute, navigate]);
  // Até saber da sessão, nenhuma tela dispara chamadas (evita 401 nas áreas da conta).
  if (!checked) return <p className="app-loading" role="status">Carregando o RiftLab…</p>;
  if (authRoute) {
    return user ? null : <AuthScreen mode={authRoute.name} next={authRoute.next} />;
  }
  return <AppContent />;
}

function AppContent() {
  const { route, navigate, href } = useRouter();
  const { hasAccount } = useAuth();
  const needsLogin = !hasAccount && ACCOUNT_AREAS.has(route.name);
  useEffect(() => {
    if (needsLogin) navigate(loginPath(href), { replace: true });
  }, [needsLogin, href, navigate]);
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
              <Link to="/" className="app__brand">
                <span className="app__mark" aria-hidden="true">R</span>RiftLab
              </Link>
            </h1>
            <MainNav route={route} />
            <div className="app__tools">
              {route.name === "home" ? null : <PlayerSearch />}
              {hasAccount ? <ProfileChip /> : null}
              <ThemeToggle />
              <UserMenu />
            </div>
          </div>
          <ContextStrip />
        </header>
        <main id="conteudo" ref={mainRef} tabIndex={-1} className={route.name === "home" ? "app__main app__main--home" : "app__main"}>
          {needsLogin ? null : renderPage(route)}
        </main>
        <footer className="app__footer">
          <div className="app__footer-row">
            <p>RiftLab</p>
            <p>Revisão de partidas concluídas e partida ao vivo sem placar (composição e elo).</p>
            <p>Retratos e ícones: Data Dragon · Emblemas: Ranked Emblems · © Riot Games</p>
          </div>
          <p lang="en" className="app__legal">
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
      <AuthProvider>
        <DemoNoticeProvider>
          <AppShell />
        </DemoNoticeProvider>
      </AuthProvider>
    </RouterProvider>
  );
}
