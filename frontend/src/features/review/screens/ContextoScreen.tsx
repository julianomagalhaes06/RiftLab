import { GlossaryDisclosure } from "../components/GlossaryDisclosure";
import { PrototypeBanner } from "../components/PrototypeBanner";
import { QualityNotice } from "../components/QualityNotice";
import { StepHeading } from "../components/StepHeading";
import type { DecisionExample, Objective } from "../types";
import { describeBudget } from "../utils";

const OBJECTIVE_OPTIONS: { value: Objective; label: string }[] = [
  { value: "BALANCED", label: "Equilibrado" },
  { value: "SURVIVAL", label: "Sobrevivência" },
  { value: "DAMAGE", label: "Dano" },
  { value: "UTILITY", label: "Utilidade" },
];

interface ContextoScreenProps {
  decision: DecisionExample;
  objective: Objective | null;
  onSetObjective: (objective: Objective) => void;
  onContinue: () => void;
  onBack: () => void;
}

export function ContextoScreen({ decision, objective, onSetObjective, onContinue, onBack }: ContextoScreenProps) {
  const budgetTone = decision.budget.source === "UNKNOWN" || decision.budget.stale ? "warning" : "info";

  return (
    <section aria-labelledby="contexto-heading">
      <StepHeading stepKey="contexto" id="contexto-heading">
        Contexto pré-decisão
      </StepHeading>
      <PrototypeBanner />

      <dl className="context-summary">
        <dt>Momento</dt>
        <dd>{decision.timeLabel}</dd>
        <dt>Função</dt>
        <dd>{decision.participantRole}</dd>
        <dt>Nível</dt>
        <dd>{decision.levelLabel}</dd>
        <dt>Composição observável</dt>
        <dd>{decision.compositionNote}</dd>
        <dt>Estado próprio</dt>
        <dd>{decision.ownState}</dd>
        <dt>Inventário</dt>
        <dd>
          <ul>
            {decision.inventorySummary.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </dd>
      </dl>

      <QualityNotice tone={budgetTone} label="Orçamento">
        {describeBudget(decision.budget)}
      </QualityNotice>

      {decision.supportLevel === "LIMITED" && decision.reasonNote ? (
        <QualityNotice tone="warning" label="Suporte limitado">
          {decision.reasonNote}
        </QualityNotice>
      ) : null}

      <GlossaryDisclosure />

      <fieldset>
        <legend>Qual necessidade você priorizaria?</legend>
        {OBJECTIVE_OPTIONS.map((option) => (
          <label className="radio-option" key={option.value}>
            <input
              type="radio"
              name="objetivo"
              value={option.value}
              checked={objective === option.value}
              onChange={() => onSetObjective(option.value)}
            />
            {option.label}
          </label>
        ))}
      </fieldset>

      <div className="actions">
        <button type="button" onClick={onBack}>
          Voltar
        </button>
        <button type="button" onClick={onContinue} disabled={objective === null}>
          Continuar
        </button>
      </div>
      {objective === null ? <p className="hint-text">Escolha uma prioridade para continuar.</p> : null}
    </section>
  );
}
