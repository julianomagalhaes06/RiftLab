import { useEffect, useRef, useState } from "react";
import {
  api,
  type ObjectiveForm,
  type ObjectiveInterpretation,
} from "../../shared/api/client";
import { OBJECTIVES, TEXT_LIMIT, objectiveLabel, objectiveOrigin } from "./objectiveText";

/**
 * Objetivo por formulário ou por texto (CC-504, UC03). O texto vira uma das quatro
 * prioridades só depois de a pessoa confirmar; ambiguidade, falha ou timeout abrem o
 * formulário e nunca mudam o objetivo já escolhido. O interpretador não decide legalidade
 * nem atributos: a prioridade confirmada só reordena candidatos legais.
 */

const CLIENT_TIMEOUT_MS = 16000;

const PROVIDER_TEXT: Readonly<Record<string, string>> = {
  RULES: "regras fixas, sem modelo de linguagem",
  LOCAL_MODEL: "modelo de linguagem local",
};

const NEEDS_INPUT_TEXT: Readonly<Record<string, string>> = {
  TIMEOUT: "A interpretação demorou demais.",
  PROVIDER_ERROR: "O interpretador não respondeu.",
  INVALID_MODEL_OUTPUT: "O interpretador devolveu uma resposta fora do formato aceito.",
  AMBIGUOUS_OR_OUT_OF_SCOPE:
    "O texto cita mais de uma prioridade sem dizer qual vem primeiro, só exclui opções ou não fala da compra.",
};

interface Props {
  idPrefix: string;
  value: ObjectiveForm;
  onChange: (objective: ObjectiveForm) => void;
}

type Pending =
  | { kind: "idle" }
  | { kind: "busy" }
  | { kind: "proposal"; result: ObjectiveInterpretation }
  | { kind: "fallback"; message: string };

export function ObjectivePicker({ idPrefix, value, onChange }: Props) {
  const [text, setText] = useState("");
  const [pending, setPending] = useState<Pending>({ kind: "idle" });
  const controller = useRef<AbortController | null>(null);
  const legend = useRef<HTMLLegendElement>(null);

  useEffect(() => () => controller.current?.abort(), []);

  async function interpret() {
    controller.current?.abort();
    const own = new AbortController();
    controller.current = own;
    const timer = window.setTimeout(() => own.abort(), CLIENT_TIMEOUT_MS);
    setPending({ kind: "busy" });
    try {
      const result = await api.interpretObjective(text.trim(), own.signal);
      if (result.status === "INTERPRETED" && result.objective) {
        setPending({ kind: "proposal", result });
      } else {
        const reason = (result.reasonCodes ?? []).map((code) => NEEDS_INPUT_TEXT[code]).find(Boolean);
        setPending({ kind: "fallback", message: reason ?? NEEDS_INPUT_TEXT.AMBIGUOUS_OR_OUT_OF_SCOPE });
        legend.current?.focus();
      }
    } catch {
      if (controller.current !== own) {
        return; // substituída por outra interpretação
      }
      setPending({ kind: "fallback", message: NEEDS_INPUT_TEXT.TIMEOUT });
      legend.current?.focus();
    } finally {
      window.clearTimeout(timer);
    }
  }

  function cancel() {
    controller.current?.abort();
    controller.current = null;
    setPending({ kind: "idle" });
  }

  const trimmed = text.trim();
  return (
    <div className="objective-picker">
      <fieldset className="field">
        <legend ref={legend} tabIndex={-1}>
          Objetivo
        </legend>
        {OBJECTIVES.map((objective) => (
          <label key={objective.value} className="radio-option">
            <input
              type="radio"
              name={`${idPrefix}-objective`}
              value={objective.value}
              checked={value.priority === objective.value}
              onChange={() => onChange({ priority: objective.value })}
            />
            {objective.label} <span className="muted">— {objective.hint}</span>
          </label>
        ))}
        <p className="muted">Selecionado: {objectiveLabel(value.priority)}, {objectiveOrigin(value)}.</p>
      </fieldset>
      <div className="field">
        <label htmlFor={`${idPrefix}-text`}>Ou descreva em uma frase (opcional)</label>
        <textarea
          id={`${idPrefix}-text`}
          maxLength={TEXT_LIMIT}
          value={text}
          onChange={(event) => setText(event.target.value)}
          aria-describedby={`${idPrefix}-text-help`}
        />
        <p id={`${idPrefix}-text-help`} className="muted">
          {text.length}/{TEXT_LIMIT} caracteres. O texto só é usado para sugerir uma das quatro
          prioridades acima; você confirma antes de valer.
        </p>
        <div className="actions">
          <button
            type="button"
            className="button button--secondary"
            disabled={trimmed.length === 0 || pending.kind === "busy"}
            onClick={interpret}
          >
            Interpretar texto
          </button>
          {pending.kind === "busy" ? (
            <button type="button" className="button button--secondary" onClick={cancel}>
              Cancelar
            </button>
          ) : null}
        </div>
      </div>
      <div aria-live="polite">
        {pending.kind === "busy" ? <p className="muted">Interpretando…</p> : null}
        {pending.kind === "proposal" && pending.result.objective ? (
          <div className="notice notice--info" role="note">
            <p>
              Entendi: <strong>{objectiveLabel(pending.result.objective.priority)}</strong> (
              {PROVIDER_TEXT[pending.result.provider] ?? pending.result.provider}, versão{" "}
              {pending.result.parserVersion}). Isso não diz qual item é melhor, só qual prioridade
              usar.
            </p>
            <div className="actions">
              <button
                type="button"
                className="button"
                onClick={() => {
                  if (pending.result.objective) {
                    onChange(pending.result.objective);
                  }
                  setPending({ kind: "idle" });
                }}
              >
                Usar este objetivo
              </button>
              <button
                type="button"
                className="button button--secondary"
                onClick={() => {
                  setPending({ kind: "idle" });
                  legend.current?.focus();
                }}
              >
                Prefiro escolher no formulário
              </button>
            </div>
          </div>
        ) : null}
        {pending.kind === "fallback" ? (
          <div className="notice notice--warning" role="note">
            <p>
              {pending.message} Escolha o objetivo no formulário acima; o objetivo selecionado não
              mudou.
            </p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
