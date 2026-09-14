import { useState } from "react";
import { PrototypeBanner } from "../components/PrototypeBanner";
import { QualityNotice } from "../components/QualityNotice";
import { StepHeading } from "../components/StepHeading";
import { EXAMPLE_PARTICIPANTS } from "../data/examples";
import type { DecisionExample } from "../types";

interface DecisoesScreenProps {
  onSelectDecision: (decision: DecisionExample) => void;
  onBackToEntry: () => void;
}

export function DecisoesScreen({ onSelectDecision, onBackToEntry }: DecisoesScreenProps) {
  const [participantId, setParticipantId] = useState(EXAMPLE_PARTICIPANTS[0].id);
  const [openNotice, setOpenNotice] = useState<DecisionExample | null>(null);

  const participant = EXAMPLE_PARTICIPANTS.find((p) => p.id === participantId) ?? EXAMPLE_PARTICIPANTS[0];

  function handleDecisionClick(decision: DecisionExample) {
    if (decision.kind === "normal") {
      setOpenNotice(null);
      onSelectDecision(decision);
      return;
    }
    setOpenNotice(decision);
  }

  return (
    <section aria-labelledby="decisoes-heading">
      <StepHeading stepKey="decisoes" id="decisoes-heading">
        Participante e pontos de compra
      </StepHeading>
      <PrototypeBanner />

      <fieldset>
        <legend>Selecionar participante</legend>
        {EXAMPLE_PARTICIPANTS.map((p) => (
          <label className="radio-option" key={p.id}>
            <input
              type="radio"
              name="participante"
              value={p.id}
              checked={participantId === p.id}
              onChange={() => {
                setParticipantId(p.id);
                setOpenNotice(null);
              }}
            />
            {p.label}
          </label>
        ))}
      </fieldset>

      {participant.decisions.length === 0 ? (
        <QualityNotice tone="info" label="Lista vazia">
          {participant.emptyReason ?? "Nenhuma decisão elegível para este participante."}
        </QualityNotice>
      ) : (
        <ul className="decision-list">
          {participant.decisions.map((decision) => (
            <li key={decision.id}>
              <button type="button" className="decision-list__item" onClick={() => handleDecisionClick(decision)}>
                {decision.label}
                {decision.kind !== "normal" ? (
                  <span className="decision-list__badge">cenário especial</span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      )}

      {openNotice ? (
        <QualityNotice
          tone="unavailable"
          label={openNotice.kind === "incompatible" ? "Fora do recorte suportado" : "Inventário incerto"}
        >
          {openNotice.incompatibleReason ?? openNotice.missingCatalogReason}
        </QualityNotice>
      ) : null}

      <div className="actions">
        <button type="button" onClick={onBackToEntry}>
          Voltar à entrada
        </button>
      </div>
    </section>
  );
}
