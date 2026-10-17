import { useEffect, useState } from "react";
import { api, type DecisionState, type DecisionSummary, type MatchDetail, type MatchParticipant } from "../../shared/api/client";
import { Link } from "../../shared/router/Link";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import {
  describeBudget,
  championName,
  formatAge,
  formatGameTime,
  ordinalLabel,
  reasonText,
  subReasonText,
} from "./format";
import { ChampionAvatar } from "../history/ChampionAvatar";
import { roleShort } from "../history/presentation";
import "./match.css";
import { useDecision } from "./useDecision";
import { DecisionExperience } from "../study/DecisionExperience";
import { ItemNames } from "../../shared/ui/ItemNames";
import { useItemName } from "../../shared/hooks/useItemName";
import { ItemIcon } from "../../shared/ui/ItemIcon";

const LEVEL_ABSENT_TEXT: Readonly<Record<string, string>> = {
  NO_PRIOR_FRAME: "não há quadro anterior à decisão",
  LEVEL_MISSING: "o quadro não informa o nível",
  LEVEL_INVALID: "o nível informado no quadro é inválido",
};

function Inventory({ decision }: { decision: DecisionState }) {
  const itemName = useItemName();
  const items = decision.inventory.items ?? [];
  const uncertain = (decision.reasons ?? []).some((reason) => reason.code === "INVENTORY_UNCERTAIN");
  const slots = items.slice(0, 6);
  return (
    <section aria-labelledby="inventory-heading" className="decision-card">
      <h3 id="inventory-heading">Inventário antes da decisão</h3>
      {uncertain ? (
        <p className="notice notice--warning" role="note">
          Inventário incerto: os itens abaixo são o melhor estado reconstruído, sem garantia.
        </p>
      ) : null}
      <div className="inventory-grid">
        <ul className="inventory-grid__slots" aria-label="Itens do inventário principal">
          {slots.map((item, index) => (
            <li key={`${item.itemId}-${index}`} className="inventory-grid__slot" title={itemName(item)}>
              <ItemIcon itemId={item.itemId} />
              {item.quantity > 1 ? <span className="inventory-grid__count">×{item.quantity}</span> : null}
              <span className="inventory-grid__name">{itemName(item)}</span>
            </li>
          ))}
          {Array.from({ length: 6 - slots.length }, (_, index) => (
            <li key={`empty-${index}`} className="inventory-grid__slot inventory-grid__slot--empty" aria-hidden="true" />
          ))}
        </ul>
        {items.length === 0 ? <p className="muted">Nenhum item no inventário principal.</p> : null}
        <p className="inventory-grid__trinket">Amuleto: {decision.inventory.trinketItemId != null
          ? <span className="item-with-icon"><ItemIcon itemId={decision.inventory.trinketItemId} /><span>{itemName({ itemId: decision.inventory.trinketItemId })}</span></span>
          : "Vazio"}</p>
      </div>
    </section>
  );
}

function Budget({ decision }: { decision: DecisionState }) {
  const budget = describeBudget(decision.budget);
  return (
    <div className="decision-stat decision-stat--gold" aria-labelledby="budget-heading">
      <h3 id="budget-heading">Orçamento</h3>
      <p className={`decision-stat__value budget budget--${budget.tone}`}>{budget.headline}</p>
      {budget.notes.map((note) => (
        <p key={note} className="muted">
          {note}
        </p>
      ))}
    </div>
  );
}

function Level({ decision }: { decision: DecisionState }) {
  const { value, frameAgeMs, absentReason } = decision.level;
  return (
    <p className="decision-stat__line">
      {typeof value === "number"
        ? `Nível ${value}${typeof frameAgeMs === "number" ? ` (quadro ${formatAge(frameAgeMs)})` : ""}`
        : `Nível não observado${absentReason ? `: ${LEVEL_ABSENT_TEXT[absentReason] ?? absentReason}` : ""}`}
    </p>
  );
}

function TechnicalDetails({ decision }: { decision: DecisionState }) {
  const versions = decision.versions;
  const subReasons = (decision.reasons ?? []).flatMap((reason) =>
    (reason.subReasons ?? []).map((sub) => `${reason.code}: ${subReasonText(sub)}`),
  );
  const rows: Array<[string, string]> = [
    ["Reconstrução", String(versions.reconstructionVersion)],
    ["Seleção", versions.selectionVersion],
    ["Esquema de features", versions.featureSchemaVersion],
    ["Recorte", versions.scopeVersion],
    ["Catálogo", versions.catalogId ?? "sem catálogo"],
    ["Regras de curadoria", versions.curationRulesVersion ?? "—"],
    ["Classificação de itens", versions.classificationVersion ?? "—"],
  ];
  return (
    <details className="details">
      <summary>Detalhes técnicos</summary>
      <dl className="details__list">
        {rows.map(([label, value]) => (
          <div key={label} className="details__row">
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {subReasons.length > 0 ? (
        <>
          <p className="details__title">Sub-motivos de inelegibilidade</p>
          <ul>
            {subReasons.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        </>
      ) : null}
    </details>
  );
}

/** Partida da decisão (campeões e times) e as outras decisões do mesmo participante.
 * Acessório: qualquer falha só omite o contexto, sem afetar a decisão. */
function useDecisionContext(matchId: string | null, participantId: number | null) {
  const key = matchId !== null && participantId !== null ? `${matchId}#${participantId}` : null;
  const [loaded, setLoaded] = useState<{ key: string; match: MatchDetail | null; decisions: DecisionSummary[] } | null>(null);
  useEffect(() => {
    if (key === null || matchId === null || participantId === null) {
      return undefined;
    }
    const controller = new AbortController();
    Promise.all([
      api.getMatch(matchId, controller.signal).catch(() => null),
      api.listDecisions(matchId, participantId, { limit: 100 }, controller.signal).then((page) => page.items).catch(() => []),
    ]).then(([match, decisions]) => {
      if (!controller.signal.aborted) setLoaded({ key, match, decisions });
    });
    return () => controller.abort();
  }, [key, matchId, participantId]);
  return loaded !== null && loaded.key === key ? loaded : null;
}

function DecisionStrip({ decision, decisions, durationSeconds }: { decision: DecisionState; decisions: DecisionSummary[]; durationSeconds: number | null }) {
  if (decisions.length < 2) return null;
  const sorted = [...decisions].sort((a, b) => a.timestampMs - b.timestampMs);
  const index = sorted.findIndex((d) => d.decisionId === decision.decisionId);
  const end = Math.max(durationSeconds ? durationSeconds * 1000 : 0, sorted.at(-1)!.timestampMs);
  const previous = index > 0 ? sorted[index - 1] : null;
  const next = index >= 0 && index < sorted.length - 1 ? sorted[index + 1] : null;
  return (
    <nav className="decision-strip" aria-label="Outras decisões deste participante">
      {previous ? (
        <Link to={{ name: "decision", decisionId: previous.decisionId }} className="decision-strip__step">← {formatGameTime(previous.timestampMs)}</Link>
      ) : <span className="decision-strip__step decision-strip__step--off" aria-hidden="true">←</span>}
      <div className="decision-strip__track">
        {sorted.map((d) => (
          <Link
            key={d.decisionId}
            to={{ name: "decision", decisionId: d.decisionId }}
            className={`decision-strip__dot${d.decisionId === decision.decisionId ? " decision-strip__dot--current" : ""}${d.eligible ? " decision-strip__dot--eligible" : ""}`}
            style={{ left: `${(d.timestampMs / end) * 100}%` }}
            aria-label={`Ir para a decisão em ${formatGameTime(d.timestampMs)}${d.decisionId === decision.decisionId ? " (atual)" : ""}`}
            aria-current={d.decisionId === decision.decisionId ? "true" : undefined}
          >
            <span className="decision-strip__label" aria-hidden="true">{formatGameTime(d.timestampMs)}</span>
          </Link>
        ))}
      </div>
      {next ? (
        <Link to={{ name: "decision", decisionId: next.decisionId }} className="decision-strip__step">{formatGameTime(next.timestampMs)} →</Link>
      ) : <span className="decision-strip__step decision-strip__step--off" aria-hidden="true">→</span>}
    </nav>
  );
}

function TeamContext({ match, participantId }: { match: MatchDetail; participantId: number }) {
  const self = match.participants.find((p) => p.participantId === participantId);
  if (!self) return null;
  const teams = [...new Set(match.participants.map((p) => p.team))].sort((a, b) => (a === self.team ? -1 : b === self.team ? 1 : a - b));
  return (
    <section aria-labelledby="context-heading" className="decision-card">
      <h3 id="context-heading">Composição dos times</h3>
      <p className="muted">Só campeões e funções, conhecidos desde a seleção; nenhum resultado posterior à compra.</p>
      {teams.map((team) => (
        <div key={team} className="team-context">
          <h4>{team === self.team ? "Seu time" : "Time inimigo"}</h4>
          <ul className="team-context__list">
            {teamsOrdered(match.participants.filter((p) => p.team === team)).map((p) => (
              <li key={p.participantId} className={p.participantId === participantId ? "team-context__member team-context__member--self" : "team-context__member"}>
                <ChampionAvatar participant={p} size={36} />
                <span><strong>{championName(p)}</strong><small>{roleShort(p.role) ?? "Função não informada"}</small></span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

const ROLE_ORDER: Readonly<Record<string, number>> = { TOP: 0, JUNGLE: 1, MIDDLE: 2, BOTTOM: 3, UTILITY: 4 };
function teamsOrdered(members: MatchParticipant[]): MatchParticipant[] {
  const rank = (p: MatchParticipant) => (p.role && p.role in ROLE_ORDER ? ROLE_ORDER[p.role] : 9);
  return [...members].sort((a, b) => rank(a) - rank(b) || a.participantId - b.participantId);
}

/** Detalhe de uma decisão (/decisions/:id): só informação ANTERIOR à decisão. */
export function DecisionDetailPage({ decisionId }: { decisionId: string }) {
  const load = useDecision(decisionId);
  const ready = load.status === "ready" ? load.decision : null;
  const context = useDecisionContext(ready?.matchId ?? null, ready?.participantId ?? null);

  if (load.status === "loading") {
    return (
      <section className="section" aria-labelledby="decision-heading">
        <h2 id="decision-heading">Decisão</h2>
        <p className="muted" aria-busy="true">
          Carregando a decisão…
        </p>
      </section>
    );
  }
  if (load.status === "error") {
    return (
      <section className="section" aria-labelledby="decision-heading">
        <h2 id="decision-heading">Decisão</h2>
        <ErrorNotice error={load.error} onRetry={load.reload} retryLabel="Tentar novamente" />
        <p>
          <Link to="/">Voltar ao início</Link>
        </p>
      </section>
    );
  }

  const decision = load.decision;
  const reasons = decision.reasons ?? [];
  const ordinal = ordinalLabel(decision.mainItemOrdinal);
  const match = context?.match ?? null;
  const participant = match?.participants.find((p) => p.participantId === decision.participantId) ?? null;
  return (
    <section className="section decision-page" aria-labelledby="decision-heading">
      <Link
        to={{ name: "match", matchId: decision.matchId, participantId: decision.participantId }}
        className="match-back"
      >
        Voltar às decisões da partida {decision.matchId}
      </Link>
      <header className="decision-hero">
        {participant ? <ChampionAvatar participant={participant} size={56} className="decision-hero__portrait" /> : null}
        <div>
          <h2 id="decision-heading">
            Decisão em {formatGameTime(decision.timestampMs)} — participante {decision.participantId}
          </h2>
          <p className="decision-hero__meta">
            {participant ? <strong>{championName(participant)}</strong> : null}
            {participant?.role ? <span>{roleShort(participant.role)}</span> : null}
            {ordinal ? <span>{ordinal}</span> : null}
            <span className={`badge ${decision.eligible ? "badge--eligible" : "badge--ineligible"}`}>
              {decision.eligible ? "Elegível para análise" : "Inelegível para análise"}
            </span>
          </p>
        </div>
      </header>
      <DecisionStrip decision={decision} decisions={context?.decisions ?? []} durationSeconds={match?.durationSeconds ?? null} />
      {reasons.length > 0 ? (
        <div className="decision-reasons" role="note">
          <p>Por que esta compra não pode ser analisada:</p>
          <ul className="reasons">
            {reasons.map((reason) => (
              <li key={reason.code}>{reasonText(reason.code)}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <ItemNames version={decision.versions.dataDragonVersion}>
        <div className="decision-grid">
          <div className="decision-grid__main">
            <div className="decision-stats">
              <div className="decision-stat">
                <h3>Momento</h3>
                <p className="decision-stat__value">{formatGameTime(decision.timestampMs)}</p>
                <Level decision={decision} />
              </div>
              <Budget decision={decision} />
            </div>
            <Inventory decision={decision} />
          </div>
          {match ? <TeamContext match={match} participantId={decision.participantId} /> : null}
        </div>
        {decision.eligible ? <DecisionExperience decisionId={decision.decisionId} /> : null}
      </ItemNames>
      <TechnicalDetails decision={decision} />
    </section>
  );
}
