import { useState } from "react";
import { TRANSFER_CASES } from "./data/examples";
import { AvaliacaoEsquemaScreen } from "./screens/AvaliacaoEsquemaScreen";
import { ComparacaoScreen } from "./screens/ComparacaoScreen";
import { ContextoScreen } from "./screens/ContextoScreen";
import { DecisoesScreen } from "./screens/DecisoesScreen";
import { EntradaScreen } from "./screens/EntradaScreen";
import { EscolhaScreen } from "./screens/EscolhaScreen";
import { OperacaoEsquemaScreen } from "./screens/OperacaoEsquemaScreen";
import { ProgressoScreen } from "./screens/ProgressoScreen";
import { TransferenciaScreen } from "./screens/TransferenciaScreen";
import type { AttemptState, DecisionExample, Objective } from "./types";

type Step =
  | "entrada"
  | "progresso"
  | "decisoes"
  | "contexto"
  | "escolha"
  | "comparacao"
  | "transferencia"
  | "avaliacao-esquema"
  | "operacao-esquema";

function freshAttempt(decisionId: string): AttemptState {
  return {
    decisionId,
    objective: null,
    selectedItemId: null,
    skipped: false,
    answered: false,
    rationale: "",
    hintLevel: 0,
    revealedAt: null,
    revealedDirect: false,
  };
}

interface ReviewPrototypeProps {
  onExit: () => void;
}

export function ReviewPrototype({ onExit }: ReviewPrototypeProps) {
  const [step, setStep] = useState<Step>("entrada");
  const [progressoOutcome, setProgressoOutcome] = useState<"success" | "error">("success");
  const [decision, setDecision] = useState<DecisionExample | null>(null);
  const [attempt, setAttempt] = useState<AttemptState | null>(null);
  const [transferIndex, setTransferIndex] = useState(0);

  function selectDecision(next: DecisionExample) {
    setDecision(next);
    setAttempt(freshAttempt(next.id));
    setStep("contexto");
  }

  function resetToEntry() {
    setStep("entrada");
    setDecision(null);
    setAttempt(null);
  }

  return (
    <main className="review-prototype">
      <div className="review-prototype__exit">
        <button type="button" onClick={onExit}>
          Sair do protótipo
        </button>
      </div>

      {step === "entrada" ? (
        <EntradaScreen
          onOpenSuccess={() => {
            setProgressoOutcome("success");
            setStep("progresso");
          }}
          onOpenUnavailable={() => {
            setProgressoOutcome("error");
            setStep("progresso");
          }}
          onOpenSchema={(schema) => setStep(schema === "avaliacao" ? "avaliacao-esquema" : "operacao-esquema")}
        />
      ) : null}

      {step === "progresso" ? (
        <ProgressoScreen
          outcome={progressoOutcome}
          onContinue={() => setStep("decisoes")}
          onBackToEntry={() => setStep("entrada")}
        />
      ) : null}

      {step === "decisoes" ? (
        <DecisoesScreen onSelectDecision={selectDecision} onBackToEntry={() => setStep("entrada")} />
      ) : null}

      {step === "contexto" && decision && attempt ? (
        <ContextoScreen
          decision={decision}
          objective={attempt.objective}
          onSetObjective={(objective: Objective) => setAttempt((prev) => (prev ? { ...prev, objective } : prev))}
          onContinue={() => setStep("escolha")}
          onBack={() => setStep("decisoes")}
        />
      ) : null}

      {step === "escolha" && decision && attempt ? (
        <EscolhaScreen
          decision={decision}
          attempt={attempt}
          onChooseAlternative={(itemId) =>
            setAttempt((prev) => (prev ? { ...prev, selectedItemId: itemId, skipped: false } : prev))
          }
          onChooseNaoSei={() =>
            setAttempt((prev) => (prev ? { ...prev, selectedItemId: null, skipped: true } : prev))
          }
          onSetRationale={(rationale) => setAttempt((prev) => (prev ? { ...prev, rationale } : prev))}
          onHintLevel={(level) =>
            setAttempt((prev) => (prev ? { ...prev, hintLevel: Math.max(prev.hintLevel, level) as 0 | 1 | 2 } : prev))
          }
          onSubmitAnswer={() => setAttempt((prev) => (prev ? { ...prev, answered: true } : prev))}
          onRevealAfterAnswer={() => {
            setAttempt((prev) =>
              prev ? { ...prev, revealedAt: new Date().toISOString(), revealedDirect: false } : prev,
            );
            setStep("comparacao");
          }}
          onRevealDirect={() => {
            setAttempt((prev) =>
              prev
                ? { ...prev, answered: true, revealedAt: new Date().toISOString(), revealedDirect: true }
                : prev,
            );
            setStep("comparacao");
          }}
          onBack={() => setStep("decisoes")}
        />
      ) : null}

      {step === "comparacao" && decision && attempt ? (
        <ComparacaoScreen
          decision={decision}
          attempt={attempt}
          onNewAttempt={() => {
            setDecision(null);
            setAttempt(null);
            setStep("decisoes");
          }}
          onContinueToTransfer={() => {
            setTransferIndex((current) => (current + 1) % TRANSFER_CASES.length);
            setStep("transferencia");
          }}
        />
      ) : null}

      {step === "transferencia" ? (
        <TransferenciaScreen
          transferCase={TRANSFER_CASES[transferIndex]}
          onFinish={(destination) => {
            if (destination === "decisoes") {
              setDecision(null);
              setAttempt(null);
              setStep("decisoes");
            } else {
              resetToEntry();
            }
          }}
        />
      ) : null}

      {step === "avaliacao-esquema" ? <AvaliacaoEsquemaScreen onBack={() => setStep("entrada")} /> : null}
      {step === "operacao-esquema" ? <OperacaoEsquemaScreen onBack={() => setStep("entrada")} /> : null}
    </main>
  );
}
