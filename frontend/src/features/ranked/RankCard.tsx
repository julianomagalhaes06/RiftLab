import type { ReactNode } from "react";
import { DIVISIONS, TIER_NAMES, emblemSrc, isApex, nextStep, rankTitle, type Tier } from "./tiers";
import "./rank-card.css";

export type RankCardRank = { tier: Tier; division: string | null; leaguePoints: number };
export type RankCardChange = { tone: "win" | "loss" | "neutral"; text: string };

/** Card de elo do padrão Arena (CC-1203; `rank_panel` do protótipo): emblema com halo, elo na cor
 * do elo, placas IV → I com o próximo elo, barra de PDL de 0 a 100 e faixa de variação. Elos do
 * topo trocam a trilha por "Sem divisões · PDL sem teto". Só apresenta: quem busca os dados
 * (Minha posição, página do jogador) passa o elo, a legenda e os controles. */
export function RankCard({
  heading,
  controls,
  rank,
  caption,
  change,
  children,
  size = "compact",
}: {
  heading: ReactNode;
  controls?: ReactNode;
  rank: RankCardRank | null;
  caption?: ReactNode;
  change?: RankCardChange | null;
  children?: ReactNode;
  size?: "compact" | "large";
}) {
  const tier = rank?.tier ?? null;
  return (
    <section className={`rank-card rank-card--${size} surface surface--rank brackets`} data-tier={tier ?? "NONE"}>
      <div className="rank-card__top">
        {heading}
        <span className="rank-card__server"><span className="sr-only">Servidor </span>BR1</span>
      </div>
      {controls}
      {rank ? <RankBody rank={rank} caption={caption} /> : null}
      {children}
      {rank && change ? (
        <p className={`rank-card__change rank-card__change--${change.tone}`}>
          {change.tone === "win" ? <span aria-hidden="true">▲ </span> : change.tone === "loss" ? <span aria-hidden="true">▼ </span> : null}
          {change.text}
        </p>
      ) : null}
    </section>
  );
}

function RankBody({ rank, caption }: { rank: RankCardRank; caption?: ReactNode }) {
  const apex = isApex(rank.tier);
  const title = rankTitle(rank.tier, rank.division);
  const lp = rank.leaguePoints;
  const next = nextStep(rank.tier, rank.division);
  const nextName = next ? rankTitle(next.tier, next.division) : null;
  return (
    <>
      <p className="sr-only">Elo {title}, {lp}{apex ? "" : " de 100"} PDL</p>
      <div className="rank-card__emblem" aria-hidden="true">
        <img src={emblemSrc(rank.tier)} alt="" width="170" height="170" />
      </div>
      <p className="rank-card__title" aria-hidden="true">{title}</p>
      {caption ? <p className="rank-card__caption">{caption}</p> : null}
      {apex ? (
        <>
          <div className="rank-card__track" aria-hidden="true">
            <span className="rank-plate-slot rank-plate-slot--now rank-plate-slot--wide">
              <Plate label="Sem divisões · PDL sem teto" state="now" />
            </span>
          </div>
          <div className="rank-card__lp-head">
            <span>PDL acumulados</span>
            <strong><span className="rank-card__lp-value">{lp}</span> PDL</strong>
          </div>
          <p className="rank-card__goal">Elos do topo não têm divisões.</p>
        </>
      ) : (
        <>
          <DivisionTrack tier={rank.tier} division={rank.division} />
          <div className="rank-card__lp-head" aria-hidden="true">
            <span>PDL na divisão</span>
            <strong><span className="rank-card__lp-value">{lp}</span> / 100</strong>
          </div>
          <LpBar lp={lp} />
          <div className="rank-card__scale">
            <span aria-hidden="true">0</span>
            <p className="rank-card__goal">
              Faltam {Math.max(0, 100 - lp)} PDL para{" "}
              <span className="rank-card__goal-target">{nextName ?? "o próximo elo"}</span>
            </p>
            <span aria-hidden="true">100</span>
          </div>
        </>
      )}
    </>
  );
}

function Plate({ label, state }: { label: string; state: "done" | "now" | "next" }) {
  return (
    <span className={`rank-plate rank-plate--${state}`}>
      <span className="rank-plate__face">{label}</span>
    </span>
  );
}

function DivisionTrack({ tier, division }: { tier: Tier; division: string | null }) {
  const current = DIVISIONS.indexOf((division ?? "") as (typeof DIVISIONS)[number]);
  const nextTier = nextStep(tier, "I")?.tier ?? null;
  return (
    <div className="rank-card__track">
      <span className="sr-only">
        Divisão {division} de IV a I{nextTier ? `; depois vem ${TIER_NAMES[nextTier]}` : ""}.
      </span>
      <div className="rank-card__plates" aria-hidden="true">
        {DIVISIONS.map((label, index) => (
          <span key={label} className={`rank-plate-slot${index === current ? " rank-plate-slot--now" : ""}`}>
            <Plate label={label} state={index < current ? "done" : index === current ? "now" : "next"} />
          </span>
        ))}
      </div>
      {nextTier ? (
        <div className="rank-card__next" aria-hidden="true">
          <svg width="10" height="16" viewBox="0 0 10 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="m2 2 6 6-6 6" /></svg>
          <span className="rank-card__next-emblem" data-tier={nextTier}>
            <img src={emblemSrc(nextTier)} alt="" width="40" height="40" />
            <span>{isApex(nextTier) ? TIER_NAMES[nextTier] : `${TIER_NAMES[nextTier]} IV`}</span>
          </span>
        </div>
      ) : null}
    </div>
  );
}

function LpBar({ lp }: { lp: number }) {
  const width = Math.max(0, Math.min(lp, 100));
  return (
    <div className="lp-bar" role="img" aria-label={`${lp} de 100 pontos de liga na divisão`}>
      <div className="lp-bar__track">
        <span className="lp-bar__fill" style={{ width: `${Math.max(width, 2)}%` }} />
      </div>
      {width < 100 ? <span className="lp-bar__tip" style={{ left: `${width}%` }} /> : null}
      <span className="lp-bar__ticks" />
    </div>
  );
}
