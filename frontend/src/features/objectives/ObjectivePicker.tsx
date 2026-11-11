import { useEffect, useRef, useState } from "react";
import {
  api,
  type ObjectiveForm,
  type ObjectiveInterpretation,
} from "../../shared/api/client";
import { OBJECTIVES, TEXT_LIMIT, objectiveLabel, objectiveOrigin } from "./objectiveText";
import "./objective-chat.css";

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

type Message =
  | { id: number; role: "user"; text: string }
  | { id: number; role: "assistant"; kind: "hello" | "done"; text: string }
  | { id: number; role: "assistant"; kind: "proposal"; result: ObjectiveInterpretation }
  | { id: number; role: "assistant"; kind: "fallback"; text: string };

type NewMessage = Message extends infer M ? (M extends Message ? Omit<M, "id"> : never) : never;

const SUGGESTIONS = ["Quero sobreviver mais", "Preciso de mais dano", "Quero usar mais habilidades"] as const;

const ICON = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true as const };

/** Assistente de objetivo em formato de conversa (11/11/2026): a pessoa escreve com as próprias
 * palavras, o interpretador (regras ou o modelo local) propõe UMA das quatro prioridades e ela
 * confirma; sem entender, ele pergunta de volta com as opções. As opções também ficam como
 * escolha direta. Nada aqui recomenda item: a prioridade só reordena candidatos legais. */
export function ObjectivePicker({ idPrefix, value, onChange }: Props) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [messages, setMessages] = useState<Message[]>([
    { id: 0, role: "assistant", kind: "hello", text: "Qual é o seu objetivo nesta compra? Escreva com suas palavras ou escolha uma opção abaixo." },
  ]);
  const nextId = useRef(1);
  // Só a proposta mais recente pode ser confirmada, e editar o texto a descarta.
  const [activeProposal, setActiveProposal] = useState<number | null>(null);
  const controller = useRef<AbortController | null>(null);
  const sent = useRef(""); // a frase em interpretação, devolvida ao campo se a pessoa cancelar
  const legend = useRef<HTMLLegendElement>(null);
  const log = useRef<HTMLOListElement>(null);

  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    const element = log.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [messages, busy]);

  function push(message: NewMessage): number {
    const id = nextId.current++;
    setMessages((current) => [...current, { ...message, id } as Message]);
    return id;
  }

  async function interpret(raw: string) {
    const phrase = raw.trim();
    if (!phrase || busy) return;
    controller.current?.abort();
    const own = new AbortController();
    controller.current = own;
    const timer = window.setTimeout(() => own.abort(), CLIENT_TIMEOUT_MS);
    push({ role: "user", text: phrase });
    sent.current = raw;
    setText("");
    setBusy(true);
    try {
      const result = await api.interpretObjective(phrase, own.signal);
      if (result.status === "INTERPRETED" && result.objective) {
        setActiveProposal(push({ role: "assistant", kind: "proposal", result }));
      } else {
        const reason = (result.reasonCodes ?? []).map((code) => NEEDS_INPUT_TEXT[code]).find(Boolean);
        push({ role: "assistant", kind: "fallback", text: reason ?? NEEDS_INPUT_TEXT.AMBIGUOUS_OR_OUT_OF_SCOPE });
        legend.current?.focus();
      }
    } catch {
      if (controller.current !== own) {
        return; // substituída por outra interpretação ou cancelada
      }
      // abortada pelo relógio do cliente = demorou demais; qualquer outra falha = sem resposta
      push({ role: "assistant", kind: "fallback", text: own.signal.aborted ? NEEDS_INPUT_TEXT.TIMEOUT : NEEDS_INPUT_TEXT.PROVIDER_ERROR });
      legend.current?.focus();
    } finally {
      window.clearTimeout(timer);
      if (controller.current === own) setBusy(false);
    }
  }

  function cancel() {
    controller.current?.abort();
    controller.current = null;
    setBusy(false);
    setText(sent.current);
  }

  function choose(objective: ObjectiveForm, announce: boolean) {
    onChange(objective);
    setActiveProposal(null);
    if (announce) {
      push({ role: "assistant", kind: "done", text: `Pronto: objetivo ${objectiveLabel(objective.priority)}.` });
      legend.current?.focus();
    }
  }

  return (
    <div className="objective-chat">
      <div className="objective-chat__head">
        <span className="objective-chat__avatar" aria-hidden="true">R</span>
        <div>
          <p className="objective-chat__title">Assistente de objetivo</p>
          <p className="objective-chat__sub">Só traduz o seu objetivo em uma prioridade; não escolhe item.</p>
        </div>
      </div>
      <ol ref={log} className="objective-chat__log" aria-live="polite" aria-label="Conversa sobre o objetivo">
        {messages.map((message) => (
          <li key={message.id} className={`objective-chat__msg objective-chat__msg--${message.role}`}>
            {message.role === "user" ? <p>{message.text}</p> : message.kind === "proposal" && message.result.objective ? (
              <>
                <p>
                  Entendi: <strong>{objectiveLabel(message.result.objective.priority)}</strong>
                  {" "}({OBJECTIVES.find((o) => o.value === message.result.objective?.priority)?.hint}).
                  Isso não diz qual item é melhor, só qual prioridade usar.
                </p>
                <p className="objective-chat__meta">{PROVIDER_TEXT[message.result.provider] ?? message.result.provider}, versão {message.result.parserVersion}</p>
                {message.id === activeProposal ? (
                  <div className="objective-chat__actions">
                    <button type="button" className="button" onClick={() => { if (message.result.objective) choose(message.result.objective, true); }}>
                      Usar este objetivo
                    </button>
                    <button type="button" className="button button--secondary" onClick={() => { setActiveProposal(null); push({ role: "assistant", kind: "done", text: "Tudo bem. Escolha uma das opções abaixo." }); legend.current?.focus(); }}>
                      Prefiro escolher no formulário
                    </button>
                  </div>
                ) : null}
              </>
            ) : message.kind === "fallback" ? (
              <p>{message.text} Escolha uma das opções abaixo; o objetivo selecionado não mudou.</p>
            ) : <p>{"text" in message ? message.text : null}</p>}
          </li>
        ))}
        {busy ? (
          <li className="objective-chat__msg objective-chat__msg--assistant objective-chat__typing">
            <span className="sr-only">Interpretando…</span>
            <span aria-hidden="true" /><span aria-hidden="true" /><span aria-hidden="true" />
          </li>
        ) : null}
      </ol>

      <div className="objective-chat__suggestions" role="group" aria-label="Sugestões de frase">
        {SUGGESTIONS.map((suggestion) => (
          <button key={suggestion} type="button" className="objective-chat__suggestion" disabled={busy} onClick={() => void interpret(suggestion)}>
            {suggestion}
          </button>
        ))}
      </div>

      <form className="objective-chat__composer" onSubmit={(event) => { event.preventDefault(); void interpret(text); }}>
        <label htmlFor={`${idPrefix}-text`} className="sr-only">Ou descreva em uma frase (opcional)</label>
        <textarea
          id={`${idPrefix}-text`}
          rows={1}
          maxLength={TEXT_LIMIT}
          value={text}
          placeholder="Ex.: quero aguentar mais o burst do Zed"
          onChange={(event) => {
            setText(event.target.value);
            setActiveProposal(null); // uma sugestão vale só para o texto que a gerou
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void interpret(text);
            }
          }}
          aria-describedby={`${idPrefix}-text-help`}
        />
        {/* Cancelar fica ao lado, nunca no lugar do enviar: trocar o botão sob o cursor fazia o
            mesmo clique cancelar e reenviar. */}
        {busy ? <button type="button" className="objective-chat__send objective-chat__send--cancel" onClick={cancel}>Cancelar</button> : null}
        <button type="submit" className="objective-chat__send" aria-label="Interpretar texto" disabled={busy || text.trim().length === 0}>
          <svg {...ICON}><path d="M5 12h14M13 6l6 6-6 6" /></svg>
        </button>
      </form>
      <p id={`${idPrefix}-text-help`} className="objective-chat__help">
        {text.length}/{TEXT_LIMIT} caracteres · Enter envia, Shift+Enter quebra a linha. Você confirma antes de valer.
      </p>

      <fieldset className="objective-chat__options">
        <legend ref={legend} tabIndex={-1}>Objetivo</legend>
        {OBJECTIVES.map((objective) => (
          <label key={objective.value} className="objective-option" title={objective.hint}>
            <input
              type="radio"
              name={`${idPrefix}-objective`}
              value={objective.value}
              checked={value.priority === objective.value}
              onChange={() => choose({ priority: objective.value }, false)}
            />
            <span className="objective-option__label">{objective.label}</span>
            <span className="objective-option__hint">— {objective.hint}</span>
          </label>
        ))}
      </fieldset>
      <p className="objective-chat__selected">Selecionado: {objectiveLabel(value.priority)}, {objectiveOrigin(value)}.</p>
    </div>
  );
}
