import { PrototypeBanner } from "../components/PrototypeBanner";
import { StepHeading } from "../components/StepHeading";
import type { AttemptState, DecisionExample } from "../types";

const NAO_SEI = "nao-sei";

interface EscolhaScreenProps {
  decision: DecisionExample;
  attempt: AttemptState;
  onChooseAlternative: (itemId: string) => void;
  onChooseNaoSei: () => void;
  onSetRationale: (text: string) => void;
  onHintLevel: (level: 1 | 2) => void;
  onSubmitAnswer: () => void;
  onRevealAfterAnswer: () => void;
  onRevealDirect: () => void;
  onBack: () => void;
}

export function EscolhaScreen({
  decision,
  attempt,
  onChooseAlternative,
  onChooseNaoSei,
  onSetRationale,
  onHintLevel,
  onSubmitAnswer,
  onRevealAfterAnswer,
  onRevealDirect,
  onBack,
}: EscolhaScreenProps) {
  const selectedValue = attempt.skipped ? NAO_SEI : (attempt.selectedItemId ?? "");
  const canSubmit = attempt.selectedItemId !== null || attempt.skipped;

  return (
    <section aria-labelledby="escolha-heading">
      <StepHeading stepKey="escolha" id="escolha-heading">
        Escolha e pistas
      </StepHeading>
      <PrototypeBanner />

      <fieldset disabled={attempt.answered}>
        <legend>Escolha uma alternativa em ordem neutra</legend>
        {decision.alternatives.map((alternative) => (
          <label className="radio-option" key={alternative.id}>
            <input
              type="radio"
              name="alternativa"
              value={alternative.id}
              checked={selectedValue === alternative.id}
              onChange={() => onChooseAlternative(alternative.id)}
            />
            {alternative.label}
          </label>
        ))}
        <label className="radio-option">
          <input
            type="radio"
            name="alternativa"
            value={NAO_SEI}
            checked={selectedValue === NAO_SEI}
            onChange={onChooseNaoSei}
          />
          Não sei
        </label>
      </fieldset>

      <details onToggle={(event) => event.currentTarget.open && onHintLevel(1)}>
        <summary>Pista 1 — observar ameaças</summary>
        <p>{decision.compositionNote}</p>
      </details>

      <details onToggle={(event) => event.currentTarget.open && onHintLevel(2)}>
        <summary>Pista 2 — conferir custo/atributos</summary>
        <ul>
          {decision.alternatives.map((alternative) => (
            <li key={alternative.id}>
              {alternative.label}: {alternative.tags.join(", ")} — custo restante{" "}
              {alternative.remainingCost !== null ? alternative.remainingCost : "indisponível"}
            </li>
          ))}
        </ul>
      </details>

      <div className="field">
        <label htmlFor="rationale-input">Justificativa (opcional, até 1000 caracteres)</label>
        <textarea
          id="rationale-input"
          maxLength={1000}
          value={attempt.rationale}
          disabled={attempt.answered}
          onChange={(event) => onSetRationale(event.target.value)}
        />
      </div>

      <div className="actions">
        <button type="button" onClick={onBack}>
          Voltar
        </button>
        {!attempt.answered ? (
          <button type="button" onClick={onSubmitAnswer} disabled={!canSubmit}>
            Registrar resposta
          </button>
        ) : (
          <button type="button" onClick={onRevealAfterAnswer}>
            Revelar comparação
          </button>
        )}
      </div>
      {!canSubmit && !attempt.answered ? (
        <p className="hint-text">Escolha uma alternativa ou &quot;Não sei&quot; para registrar a resposta.</p>
      ) : null}

      <section aria-labelledby="revisao-direta-heading" className="direct-review">
        <h3 id="revisao-direta-heading">Revisão direta</h3>
        <p>Revele a comparação sem responder, sem impedir o percurso do modo Estudo.</p>
        <button type="button" onClick={onRevealDirect}>
          Revelar sem responder
        </button>
      </section>
    </section>
  );
}
