import { useState } from "react";
import { PrototypeBanner } from "../components/PrototypeBanner";
import { QualityNotice } from "../components/QualityNotice";
import { StepHeading } from "../components/StepHeading";
import type { AttemptState, DecisionExample } from "../types";

interface ComparacaoScreenProps {
  decision: DecisionExample;
  attempt: AttemptState;
  onNewAttempt: () => void;
  onContinueToTransfer: () => void;
}

export function ComparacaoScreen({ decision, attempt, onNewAttempt, onContinueToTransfer }: ComparacaoScreenProps) {
  const [reflection, setReflection] = useState("");

  return (
    <section aria-labelledby="comparacao-heading">
      <StepHeading stepKey="comparacao" id="comparacao-heading">
        Comparação
      </StepHeading>
      <PrototypeBanner />

      {decision.analysisStatus === "UNSUPPORTED" ? (
        <QualityNotice tone="unavailable" label="Sem recomendação disponível">
          {decision.reasonNote ?? "Nenhuma alternativa é apresentada como recomendada neste cenário."} As opções
          seguem comparáveis por atributos abaixo.
        </QualityNotice>
      ) : decision.supportLevel === "LIMITED" ? (
        <QualityNotice tone="warning" label="Suporte limitado">
          {decision.reasonNote ?? "Poucos exemplos semelhantes no contexto."}
        </QualityNotice>
      ) : (
        <QualityNotice tone="info" label="Comparação disponível">
          Comparação contextual ilustrativa; score é relativo, nunca probabilidade de vitória.
        </QualityNotice>
      )}

      <p className="disclaimer">
        A compra histórica e a alternativa mais bem colocada pelo modelo não são gabarito
        universal — servem para comparação, não para correção automática.
      </p>

      {attempt.skipped ? (
        <p>Você respondeu &quot;não sei&quot; para esta tentativa.</p>
      ) : attempt.revealedDirect && attempt.selectedItemId === null ? (
        <p>Revisão direta: nenhuma resposta foi registrada antes de revelar.</p>
      ) : null}

      <ul className="comparison-list">
        {decision.alternatives.map((alternative) => {
          const isChoice = attempt.selectedItemId === alternative.id;
          return (
            <li key={alternative.id} className={isChoice ? "comparison-list__item--chosen" : undefined}>
              <p>
                <strong>{alternative.label}</strong>
                {isChoice ? <span className="tag-chosen"> — sua escolha</span> : null}
              </p>
              <p>Atributos: {alternative.tags.join(", ")}</p>
              <p>
                Custo restante:{" "}
                {alternative.remainingCost !== null ? alternative.remainingCost : "indisponível"}
              </p>
              <p>{alternative.postRevealNote}</p>
            </li>
          );
        })}
      </ul>

      <div className="field">
        <label htmlFor="reflection-input">
          Reflexão: o que mudaria se a ameaça ou o orçamento fosse diferente? (opcional)
        </label>
        <textarea
          id="reflection-input"
          maxLength={1000}
          value={reflection}
          onChange={(event) => setReflection(event.target.value)}
        />
      </div>

      <div className="actions">
        <button type="button" onClick={onNewAttempt}>
          Nova tentativa
        </button>
        <button type="button" onClick={onContinueToTransfer}>
          Continuar para cenário de transferência
        </button>
      </div>
    </section>
  );
}
