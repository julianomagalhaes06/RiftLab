import { useEffect, useState } from "react";
import { api, type DecisionState, type MatchParticipant } from "../../shared/api/client";
import { Link } from "../../shared/router/Link";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import {
  describeBudget,
  formatAge,
  formatGameTime,
  itemLabel,
  ordinalLabel,
  reasonText,
  subReasonText,
  trinketLabel,
} from "./format";
import { ChampionBadge } from "./ChampionBadge";
import { useDecision } from "./useDecision";
import { StudyPanel } from "../study/StudyPanel";

const LEVEL_ABSENT_TEXT: Readonly<Record<string, string>> = {
  NO_PRIOR_FRAME: "não há quadro anterior à decisão",
  LEVEL_MISSING: "o quadro não informa o nível",
  LEVEL_INVALID: "o nível informado no quadro é inválido",
};

function Inventory({ decision }: { decision: DecisionState }) {
  const items = decision.inventory.items ?? [];
  const uncertain = (decision.reasons ?? []).some((reason) => reason.code === "INVENTORY_UNCERTAIN");
  return (
    <section aria-labelledby="inventory-heading" className="section">
      <h3 id="inventory-heading">Inventário antes da decisão</h3>
      {uncertain ? (
        <p className="notice notice--warning" role="note">
          Inventário incerto: os itens abaixo são o melhor estado reconstruído, sem garantia.
        </p>
      ) : null}
      {items.length === 0 ? (
        <p className="muted">Nenhum item no inventário principal.</p>
      ) : (
        <ul className="list list--compact">
          {items.map((item) => (
            <li key={item.itemId} className="list__item">
              <span className="list__title">{itemLabel(item)}</span>
              {item.quantity > 1 ? <span className="tag">×{item.quantity}</span> : null}
            </li>
          ))}
        </ul>
      )}
      <p className="muted">Trinket: {trinketLabel(decision.inventory.trinketItemId)}</p>
    </section>
  );
}

function Budget({ decision }: { decision: DecisionState }) {
  const budget = describeBudget(decision.budget);
  return (
    <section aria-labelledby="budget-heading" className="section">
      <h3 id="budget-heading">Orçamento</h3>
      <p className={`budget budget--${budget.tone}`}>{budget.headline}</p>
      {budget.notes.map((note) => (
        <p key={note} className="muted">
          {note}
        </p>
      ))}
    </section>
  );
}

function Level({ decision }: { decision: DecisionState }) {
  const { value, frameAgeMs, absentReason } = decision.level;
  return (
    <p className="muted">
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

/** Campeão do participante da decisão (CC-208), lido do detalhe da partida. Acessório:
 * qualquer falha só omite o campeão, sem afetar a decisão. */
function useParticipantChampion(matchId: string | null, participantId: number | null) {
  const key = matchId !== null && participantId !== null ? `${matchId}#${participantId}` : null;
  const [loaded, setLoaded] = useState<{ key: string; participant: MatchParticipant | null } | null>(
    null,
  );
  useEffect(() => {
    if (key === null || matchId === null) {
      return undefined;
    }
    const controller = new AbortController();
    api
      .getMatch(matchId, controller.signal)
      .then((match) =>
        setLoaded({
          key,
          participant: match.participants.find((p) => p.participantId === participantId) ?? null,
        }),
      )
      .catch(() => setLoaded({ key, participant: null }));
    return () => controller.abort();
  }, [key, matchId, participantId]);
  return loaded !== null && loaded.key === key ? loaded.participant : null;
}

/** Detalhe de uma decisão (/decisions/:id): só informação ANTERIOR à decisão. */
export function DecisionDetailPage({ decisionId }: { decisionId: string }) {
  const load = useDecision(decisionId);
  const ready = load.status === "ready" ? load.decision : null;
  const participant = useParticipantChampion(ready?.matchId ?? null, ready?.participantId ?? null);

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
  return (
    <section className="section" aria-labelledby="decision-heading">
      <h2 id="decision-heading">
        Decisão em {formatGameTime(decision.timestampMs)} — participante {decision.participantId}
      </h2>
      <p className="decision__champion">
        {participant ? <ChampionBadge participant={participant} size={40} /> : null}
      </p>
      <p>
        <Link
          to={{
            name: "match",
            matchId: decision.matchId,
            participantId: decision.participantId,
          }}
        >
          Voltar às decisões da partida {decision.matchId}
        </Link>
      </p>

      <p>
        <span className={`badge ${decision.eligible ? "badge--eligible" : "badge--ineligible"}`}>
          {decision.eligible ? "Elegível para análise" : "Inelegível para análise"}
        </span>
        {ordinal ? <span className="list__meta"> {ordinal}</span> : null}
      </p>
      {reasons.length > 0 ? (
        <ul className="reasons">
          {reasons.map((reason) => (
            <li key={reason.code}>{reasonText(reason.code)}</li>
          ))}
        </ul>
      ) : null}

      <Inventory decision={decision} />
      <Level decision={decision} />
      <Budget decision={decision} />
      {decision.eligible ? <StudyPanel decisionId={decision.decisionId} /> : null}
      <TechnicalDetails decision={decision} />
    </section>
  );
}
