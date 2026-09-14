import { useEffect, useState } from "react";
import { PrototypeBanner } from "../components/PrototypeBanner";
import { QualityNotice } from "../components/QualityNotice";
import { StepHeading } from "../components/StepHeading";

type Phase = "loading" | "ready" | "failed";

interface ProgressoScreenProps {
  outcome: "success" | "error";
  onContinue: () => void;
  onBackToEntry: () => void;
}

export function ProgressoScreen({ outcome, onContinue, onBackToEntry }: ProgressoScreenProps) {
  const [attempt, setAttempt] = useState(0);
  const [phase, setPhase] = useState<Phase>("loading");
  const [phaseAttempt, setPhaseAttempt] = useState(0);

  if (attempt !== phaseAttempt) {
    setPhaseAttempt(attempt);
    setPhase("loading");
  }

  useEffect(() => {
    if (phase !== "loading") {
      return;
    }
    const timer = window.setTimeout(() => {
      setPhase(outcome === "success" ? "ready" : "failed");
    }, 600);
    return () => window.clearTimeout(timer);
  }, [phase, outcome]);

  return (
    <section aria-labelledby="progresso-heading">
      <StepHeading stepKey="progresso" id="progresso-heading">
        Progresso da importação (simulado)
      </StepHeading>
      <PrototypeBanner />

      <div role="status" aria-live="polite">
        {phase === "loading" ? <p>Carregando importação (simulado)…</p> : null}
        {phase === "ready" ? <p>Importação concluída (simulada).</p> : null}
      </div>

      {phase === "failed" ? (
        <QualityNotice tone="unavailable" label="UPSTREAM_UNAVAILABLE (ilustrativo)">
          Não foi possível concluir a importação simulada. Isto ilustra uma falha
          recuperável: é possível tentar novamente ou voltar à entrada.
        </QualityNotice>
      ) : null}

      <div className="actions">
        {phase === "ready" ? (
          <button type="button" onClick={onContinue}>
            Continuar
          </button>
        ) : null}
        {phase === "failed" ? (
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>
            Tentar novamente
          </button>
        ) : null}
        <button type="button" onClick={onBackToEntry}>
          Voltar à entrada
        </button>
      </div>
    </section>
  );
}
