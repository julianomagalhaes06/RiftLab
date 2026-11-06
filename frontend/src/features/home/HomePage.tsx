import { useState } from "react";
import { Link } from "../../shared/router/Link";
import { loginPath } from "../../shared/router/routes";
import { useAuth } from "../auth/authContext";
import { PlayerSearch } from "../players/PlayerSearch";
import { pickSplash, splashUrl, type Splash } from "./splashes";
import "./home.css";

// Uma skin por carregamento da página: navegar dentro do app e voltar ao início mantém a mesma.
let sessionSplash: Splash | null = null;
function currentSplash(): Splash {
  sessionSplash ??= pickSplash();
  return sessionSplash;
}

type IconKind = "profile" | "review" | "live" | "champions" | "decision" | "study" | "rank";

function Icon({ kind }: { kind: IconKind }) {
  const common = { width: 22, height: 22, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true as const };
  switch (kind) {
    case "profile": return <svg {...common}><circle cx="12" cy="8" r="4" /><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" /></svg>;
    case "review": return <svg {...common}><path d="M3 20h18" /><path d="M5 16l4-5 4 3 6-8" /><circle cx="19" cy="6" r="1.5" /></svg>;
    case "live": return <svg {...common}><circle cx="12" cy="12" r="2.5" /><path d="M7.5 7.5a6.4 6.4 0 0 0 0 9m9-9a6.4 6.4 0 0 1 0 9M4.6 4.6a10.5 10.5 0 0 0 0 14.8m14.8-14.8a10.5 10.5 0 0 1 0 14.8" /></svg>;
    case "champions": return <svg {...common}><path d="M12 2 20 6v6c0 5-3.5 8-8 10-4.5-2-8-5-8-10V6l8-4Z" /><path d="m9 12 2 2 4-4" /></svg>;
    case "decision": return <svg {...common}><path d="M6 3v6a6 6 0 0 0 12 0V3" /><path d="M12 15v6m-4 0h8" /></svg>;
    case "study": return <svg {...common}><path d="M2 8l10-5 10 5-10 5L2 8Z" /><path d="M6 10v5c2 2 10 2 12 0v-5" /></svg>;
    case "rank": return <svg {...common}><path d="M8 4h8v6a4 4 0 0 1-8 0V4Z" /><path d="M8 6H4v2a4 4 0 0 0 4 4m8-6h4v2a4 4 0 0 1-4 4M12 14v4m-4 3h8" /></svg>;
  }
}

const OPEN_FEATURES: readonly { icon: IconKind; title: string; text: string }[] = [
  { icon: "profile", title: "Perfil e histórico", text: "Ícone, nível, elo e as partidas ranqueadas de qualquer Riot ID do servidor BR." },
  { icon: "review", title: "Revisão da partida", text: "Placar, compras por ida à loja, ordem de habilidades, ouro e chance de vitória." },
  { icon: "live", title: "Partida ao vivo", text: "Os dois times da partida em andamento, com feitiços, runas e elo de cada jogador." },
  { icon: "champions", title: "Campeões", text: "O que costuma ser comprado com cada campeão, por etapa da build." },
];

const ACCOUNT_FEATURES: readonly { icon: IconKind; title: string; text: string }[] = [
  { icon: "decision", title: "Análise de cada compra", text: "Até três alternativas para o item comprado, com custo, justificativa e confiança." },
  { icon: "study", title: "Modo estudo", text: "Escolha antes de ver a resposta, peça pistas e guarde suas reflexões." },
  { icon: "rank", title: "Painel e posição", text: "Vincule sua conta Riot para ver sua taxa de vitória, seu elo e seu progresso." },
];

/**
 * Início público (/): vitrine com uma splash art sorteada a cada carregamento e a busca de
 * jogador no centro, como nos sites de estatísticas do jogo. Sem conta, a pessoa navega por
 * perfis e partidas; a conta serve para analisar compras e guardar o estudo.
 */
export function HomePage() {
  const { user, hasAccount } = useAuth();
  const [splash] = useState(currentSplash);
  const [artState, setArtState] = useState<"loading" | "ready" | "failed">("loading");

  return (
    <div className="home">
      <section className="home-hero" aria-labelledby="home-title" data-art={artState}>
        {artState === "failed" ? null : (
          <img
            className="home-hero__art"
            src={splashUrl(splash)}
            alt=""
            fetchPriority="high"
            decoding="async"
            onLoad={() => setArtState("ready")}
            onError={() => setArtState("failed")}
          />
        )}
        <div className="home-hero__inner">
          <p className="home-hero__kicker">
            <span className="chip">BR1</span>
            <span>Revisão pós-partida · League of Legends</span>
          </p>
          <h2 id="home-title" className="home-hero__title">
            Entenda cada compra. <span>Decida melhor na próxima.</span>
          </h2>
          <p className="home-hero__lead">
            Busque qualquer jogador pelo Riot ID e abra as partidas: placar, compras, ouro e
            chance de vitória. Sem cadastro.
          </p>
          <PlayerSearch variant="hero" />
        </div>
        {artState === "ready" ? (
          <p className="home-hero__credit">
            <span className="sr-only">Arte de fundo: </span>
            {splash.skin} <span aria-hidden="true">·</span> © Riot Games
          </p>
        ) : null}
      </section>

      <section className="home-section" aria-labelledby="home-open">
        <header className="home-section__head">
          <p className="home-section__kicker">Sem conta</p>
          <h3 id="home-open">Aberto para qualquer pessoa</h3>
        </header>
        <ul className="home-cards">
          {OPEN_FEATURES.map((feature) => (
            <li key={feature.title} className="home-card surface surface--pad">
              <span className="home-card__icon"><Icon kind={feature.icon} /></span>
              <strong>{feature.title}</strong>
              <span className="home-card__text">{feature.text}</span>
            </li>
          ))}
        </ul>
        <p className="home-section__more">
          <Link to="/campeoes">Ver padrões por campeão</Link>
        </p>
      </section>

      <section className="home-account surface surface--rank brackets" aria-labelledby="home-account">
        <div className="home-account__copy">
          <p className="home-section__kicker">{user ? `Conta ${user.username}` : "Com conta"}</p>
          <h3 id="home-account">
            {user ? "Continue de onde parou" : "Analise suas compras e guarde o que aprendeu"}
          </h3>
          <ul className="home-account__list">
            {ACCOUNT_FEATURES.map((feature) => (
              <li key={feature.title}>
                <span className="home-card__icon"><Icon kind={feature.icon} /></span>
                <span><strong>{feature.title}</strong><small>{feature.text}</small></span>
              </li>
            ))}
          </ul>
        </div>
        <div className="home-account__actions">
          {user || hasAccount ? (
            <>
              <Link to="/painel" className="button home-account__button">Ir para o Painel</Link>
              <Link to="/historico" className="button button--secondary home-account__button">Meu histórico</Link>
            </>
          ) : (
            <>
              <Link to={loginPath(null, "register")} className="button home-account__button">Criar conta</Link>
              <Link to={loginPath(null)} className="button button--secondary home-account__button">Já tenho conta</Link>
              <p className="home-account__note">A conta fica nesta instalação do RiftLab. Não pedimos e-mail.</p>
            </>
          )}
        </div>
      </section>
    </div>
  );
}
