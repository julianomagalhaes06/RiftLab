import { useEffect, useRef, useState } from "react";
import {
  api,
  reportUrl,
  type FeedbackResult,
  type LearningAttempt,
} from "../../shared/api/client";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";

/**
 * Depois da comparação (CC-505): reflexão (uma vez), exportação rastreável e julgamento
 * opcional de revisor por rubrica. Nada disso altera a análise nem alimenta treino.
 */

const REFLECTION_LIMIT = 1000;
export const RUBRIC_VERSION = "rubric_v1";
const GRADES: ReadonlyArray<{ value: string; label: string }> = [
  { value: "3", label: "3 — adequada ao contexto" },
  { value: "2", label: "2 — razoável, com ressalvas" },
  { value: "1", label: "1 — fraca para o contexto" },
  { value: "0", label: "0 — inadequada" },
  { value: "abstain", label: "Abster-se" },
];

export function Reflection({
  attempt,
  onSaved,
}: {
  attempt: LearningAttempt;
  onSaved: (attempt: LearningAttempt) => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [justSaved, setJustSaved] = useState(false);
  const savedText = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (justSaved) {
      savedText.current?.focus(); // o campo sumiu; o foco vai para a reflexão gravada
    }
  }, [justSaved]);
  return (
    <section className="section" aria-labelledby="reflection-heading">
      <h4 id="reflection-heading">Reflexão</h4>
      {attempt.reflection ? (
        <p ref={savedText} tabIndex={-1}>
          Sua reflexão registrada: <q>{attempt.reflection}</q>
        </p>
      ) : (
        <div className="field">
          <label htmlFor="reflection-text">
            O que mudaria na sua escolha se a ameaça ou o orçamento fosse diferente?
          </label>
          <textarea
            id="reflection-text"
            maxLength={REFLECTION_LIMIT}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
          <p className="muted">
            {text.length}/{REFLECTION_LIMIT} caracteres. Registrada uma vez; fica só neste computador.
          </p>
          <div className="actions">
            <button
              type="button"
              className="button button--secondary"
              disabled={busy || text.trim().length === 0}
              onClick={() => {
                setBusy(true);
                setError(null);
                api
                  .reflectAttempt(attempt.attemptId, text.trim())
                  .then((saved) => {
                    onSaved(saved);
                    setJustSaved(true);
                  })
                  .catch(setError)
                  .finally(() => setBusy(false));
              }}
            >
              Registrar reflexão
            </button>
          </div>
        </div>
      )}
      {error ? <ErrorNotice error={error} /> : null}
    </section>
  );
}

export function ExportLinks({ attempt }: { attempt: LearningAttempt }) {
  if (!attempt.analysisId) {
    return null;
  }
  return (
    <section className="section" aria-labelledby="export-heading">
      <h4 id="export-heading">Exportar relatório</h4>
      <p className="muted">
        Inclui contexto antes da compra, objetivo, alternativas, versões, limites, sua resposta
        inicial, pistas e reflexão. Sem identificadores de partida ou de jogador.
      </p>
      <div className="actions">
        <a className="button button--secondary" href={reportUrl(attempt.analysisId, "html", attempt.attemptId)} download>
          Baixar HTML (abre sem internet)
        </a>
        <a className="button button--secondary" href={reportUrl(attempt.analysisId, "json", attempt.attemptId)} download>
          Baixar JSON
        </a>
      </div>
    </section>
  );
}

export function ReviewerFeedback({ attempt }: { attempt: LearningAttempt }) {
  const candidates = attempt.comparison?.candidates ?? [];
  const [itemId, setItemId] = useState<number | null>(candidates[0]?.itemId ?? null);
  const [grade, setGrade] = useState("2");
  const [reason, setReason] = useState("");
  const [alias, setAlias] = useState("");
  const [saved, setSaved] = useState<FeedbackResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (!attempt.analysisId || candidates.length === 0) {
    return null;
  }
  const analysisId = attempt.analysisId;
  const valid = itemId !== null && reason.trim().length > 0 && alias.trim().length > 0;
  return (
    <details className="details">
      <summary>Avaliação de revisor (opcional)</summary>
      <p className="muted">
        Para quem revisa a análise com a rubrica {RUBRIC_VERSION}. É uma opinião registrada, não
        nota automática do aluno, e não é usada para treinar modelos. Use um pseudônimo.
      </p>
      <div className="field">
        <label htmlFor="feedback-item">Alternativa</label>
        <select
          id="feedback-item"
          value={itemId ?? ""}
          onChange={(event) => setItemId(Number(event.target.value))}
        >
          {candidates.map((candidate) => (
            <option key={candidate.itemId} value={candidate.itemId}>
              {candidate.name ?? `Item ${candidate.itemId}`}
            </option>
          ))}
        </select>
        <label htmlFor="feedback-grade">Nota</label>
        <select id="feedback-grade" value={grade} onChange={(event) => setGrade(event.target.value)}>
          {GRADES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <label htmlFor="feedback-reason">Motivo</label>
        <textarea
          id="feedback-reason"
          maxLength={1000}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
        <label htmlFor="feedback-alias">Pseudônimo do revisor</label>
        <input id="feedback-alias" type="text" maxLength={80} value={alias} onChange={(event) => setAlias(event.target.value)} />
      </div>
      <div className="actions">
        <button
          type="button"
          className="button button--secondary"
          disabled={!valid || busy}
          onClick={() => {
            setBusy(true);
            setError(null);
            api
              .submitFeedback(
                analysisId,
                {
                  itemId: itemId ?? 0,
                  grade: grade === "abstain" ? null : Number(grade),
                  reason: reason.trim(),
                  rubricVersion: RUBRIC_VERSION,
                  reviewerAlias: alias.trim(),
                },
                crypto.randomUUID(),
              )
              .then((result) =>
                setSaved((list) => [...list.filter((f) => f.feedbackId !== result.feedbackId), result]),
              )
              .catch(setError)
              .finally(() => setBusy(false));
          }}
        >
          Registrar julgamento
        </button>
      </div>
      {error ? <ErrorNotice error={error} /> : null}
      <div aria-live="polite">
        {saved.length > 0 ? (
          <ul>
            {saved.map((feedback) => (
              <li key={feedback.feedbackId}>
                Julgamento registrado:{" "}
                {candidates.find((c) => c.itemId === feedback.itemId)?.name ?? feedback.itemId} —{" "}
                {feedback.grade === null || feedback.grade === undefined ? "abstenção" : `nota ${feedback.grade}`}{" "}
                (revisor {feedback.reviewerRef})
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </details>
  );
}
