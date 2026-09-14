import { useState } from "react";
import { PrototypeBanner } from "../components/PrototypeBanner";
import { StepHeading } from "../components/StepHeading";
import type { TransferCase } from "../types";

const NAO_SEI = "nao-sei";

interface TransferenciaScreenProps {
  transferCase: TransferCase;
  onFinish: (destination: "decisoes" | "entrada") => void;
}

export function TransferenciaScreen({ transferCase, onFinish }: TransferenciaScreenProps) {
  const [selected, setSelected] = useState<string | null>(null);
  const [skipped, setSkipped] = useState(false);
  const [revealed, setRevealed] = useState(false);

  const selectedValue = skipped ? NAO_SEI : (selected ?? "");
  const canReveal = selected !== null || skipped;

  return (
    <section aria-labelledby="transferencia-heading">
      <StepHeading stepKey={`transferencia-${transferCase.id}`} id="transferencia-heading">
        Cenário de transferência (sem pistas)
      </StepHeading>
      <PrototypeBanner />
      <p>
        Cenário independente do conjunto didático, sem pistas. Um único cenário não mede
        habilidade nem garante generalização.
      </p>

      <dl className="context-summary">
        <dt>Momento</dt>
        <dd>{transferCase.timeLabel}</dd>
        <dt>Composição observável</dt>
        <dd>{transferCase.compositionNote}</dd>
        <dt>Estado próprio</dt>
        <dd>{transferCase.ownState}</dd>
      </dl>

      <fieldset disabled={revealed}>
        <legend>Escolha uma alternativa</legend>
        {transferCase.alternatives.map((alternative) => (
          <label className="radio-option" key={alternative.id}>
            <input
              type="radio"
              name="alternativa-transferencia"
              value={alternative.id}
              checked={selectedValue === alternative.id}
              onChange={() => {
                setSelected(alternative.id);
                setSkipped(false);
              }}
            />
            {alternative.label}
          </label>
        ))}
        <label className="radio-option">
          <input
            type="radio"
            name="alternativa-transferencia"
            value={NAO_SEI}
            checked={selectedValue === NAO_SEI}
            onChange={() => {
              setSkipped(true);
              setSelected(null);
            }}
          />
          Não sei
        </label>
      </fieldset>

      {!revealed ? (
        <div className="actions">
          <button type="button" onClick={() => setRevealed(true)} disabled={!canReveal}>
            Ver comentário breve
          </button>
        </div>
      ) : (
        <>
          <ul className="comparison-list">
            {transferCase.alternatives.map((alternative) => (
              <li key={alternative.id}>
                <p>
                  <strong>{alternative.label}</strong>
                  {selected === alternative.id ? <span className="tag-chosen"> — sua escolha</span> : null}
                </p>
                <p>{alternative.postRevealNote}</p>
              </li>
            ))}
          </ul>
          <div className="actions">
            <button type="button" onClick={() => onFinish("decisoes")}>
              Ver outras decisões
            </button>
            <button type="button" onClick={() => onFinish("entrada")}>
              Reiniciar protótipo
            </button>
          </div>
        </>
      )}
    </section>
  );
}
