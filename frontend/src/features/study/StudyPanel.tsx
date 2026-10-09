import { useState } from "react";
import {
  api,
  type AnalysisCandidate,
  type AnalysisResult,
  type LearningAttempt,
  type ObjectivePriority,
} from "../../shared/api/client";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import "../review/review.css";

/**
 * Jornada de estudo de uma decisão (CC-304/305, docs/learning-design.md): escolher o
 * objetivo, escolher entre alternativas em ordem neutra (ou "não sei"), consultar pistas
 * e só então revelar a comparação. Antes de revelar não há posição, pontuação, custo nem
 * compra histórica na tela.
 */

const OBJECTIVES: ReadonlyArray<{ value: ObjectivePriority; label: string; hint: string }> = [
  { value: "BALANCED", label: "Equilibrado", hint: "sem preferência por um tipo de atributo" },
  { value: "SURVIVAL", label: "Sobrevivência", hint: "armadura, resistência mágica e vida" },
  { value: "DAMAGE", label: "Dano", hint: "dano, crítico, velocidade de ataque e penetração" },
  { value: "UTILITY", label: "Utilidade", hint: "aceleração de habilidade, mana e mobilidade" },
];

const ENGINE_TEXT: Readonly<Record<string, string>> = {
  POPULARITY: "Popularidade nas decisões de referência deste campeão nesta etapa da build",
  RULES: "Regras explícitas (aproveitar componentes e custo restante)",
};

const REASON_TEXT: Readonly<Record<string, string>> = {
  PATCH_UNSUPPORTED: "Não há regras de itens para o patch desta partida.",
  NO_LEGAL_CANDIDATES: "Nenhum item principal legal estava disponível neste momento.",
  INVALID_STATE: "O estado antes da compra não pôde ser reconstruído com segurança.",
  INVENTORY_UNCERTAIN: "O inventário antes desta compra é incerto.",
  OUT_OF_SCOPE: "Esta decisão está fora do recorte analisado.",
  NO_POPULARITY_FOR_CELL: "Não há decisões de referência deste campeão nesta etapa; a ordem vem das regras explícitas.",
};

function reasonText(code: string): string {
  return REASON_TEXT[code] ?? `Motivo: ${code}`;
}

function name(option: { itemId: number; name?: string | null }): string {
  return option.name ?? `Item ${option.itemId}`;
}

function Comparison({ attempt }: { attempt: LearningAttempt }) {
  const comparison = attempt.comparison;
  if (!comparison) {
    return null;
  }
  const candidates: AnalysisCandidate[] = comparison.candidates ?? [];
  const reasons = comparison.support?.reasonCodes ?? [];
  return (
    <section className="section" aria-labelledby="comparison-heading">
      <h3 id="comparison-heading">Comparação</h3>
      <p className="muted">
        Objetivo: {OBJECTIVES.find((o) => o.value === comparison.objective.priority)?.label} ·
        Critério: {ENGINE_TEXT[comparison.engine] ?? comparison.engine}
      </p>
      {candidates.length === 0 ? (
        <div className="notice notice--info" role="note">
          <p>Não há comparação disponível para esta decisão.</p>
          <ul>
            {(reasons.length > 0 ? reasons : ["NO_LEGAL_CANDIDATES"]).map((code) => (
              <li key={code}>{reasonText(code)}</li>
            ))}
          </ul>
        </div>
      ) : (
        <>
          {candidates.length < 3 ? (
            <p className="muted">
              Só {candidates.length === 1 ? "1 alternativa legal" : `${candidates.length} alternativas legais`} neste
              momento.
            </p>
          ) : null}
          <ol className="comparison-list">
            {candidates.map((candidate) => {
              const chosen = attempt.selectedItemId === candidate.itemId;
              const historical = attempt.historicalItemId === candidate.itemId;
              const moved =
                candidate.baseRank != null && candidate.baseRank !== candidate.rank
                  ? candidate.baseRank > candidate.rank
                    ? "subiu pela prioridade do objetivo"
                    : "desceu pela prioridade do objetivo"
                  : null;
              return (
                <li key={candidate.itemId} className={chosen ? "comparison-list__item--chosen" : undefined}>
                  <strong>
                    {candidate.rank}. {name(candidate)}
                  </strong>
                  {chosen ? <span className="tag-chosen"> — sua escolha</span> : null}
                  {historical ? <span className="tag"> compra feita na partida (histórico)</span> : null}
                  {moved ? <span className="muted"> ({moved})</span> : null}
                  <ul>
                    {(candidate.explanations ?? []).map((explanation) => (
                      <li key={explanation.text}>
                        {explanation.text} <span className="muted">fonte: {explanation.source}</span>
                      </li>
                    ))}
                  </ul>
                </li>
              );
            })}
          </ol>
          {reasons.map((code) => (
            <p key={code} className="muted">
              {reasonText(code)}
            </p>
          ))}
        </>
      )}
      {attempt.historicalItemId != null &&
      !candidates.some((candidate) => candidate.itemId === attempt.historicalItemId) ? (
        <p className="muted">A compra feita na partida não está entre as alternativas mostradas.</p>
      ) : null}
      <p className="muted">
        {comparison.scoreNote ?? "Pontuações são relativas a esta decisão; não são probabilidades."} A compra
        feita na partida não é um gabarito: é só o que aconteceu.
      </p>
      <details className="details">
        <summary>Versões usadas</summary>
        <ul>
          <li>Catálogo: {comparison.versions.catalog}</li>
          <li>Critério: {comparison.versions.ranking ?? comparison.engine}</li>
          <li>Objetivo: {comparison.versions.objective ?? "—"}</li>
          <li>Regras de itens: {comparison.versions.itemRules ?? "—"}</li>
          <li>Modelo: {comparison.versions.model ?? "nenhum (baseline explícito)"}</li>
        </ul>
      </details>
    </section>
  );
}

const AFFORDABILITY_TEXT: Readonly<Record<string, string>> = {
  WITHIN_USER_BUDGET: "cabe no ouro informado",
  ABOVE_USER_BUDGET: "não cabe no ouro informado",
  UNKNOWN: "sem orçamento conhecido",
};

function Scenario({ attempt }: { attempt: LearningAttempt }) {
  const [gold, setGold] = useState("");
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const comparison = attempt.comparison;
  if (!comparison || (comparison.candidates ?? []).length === 0) {
    return null;
  }
  const value = Number.parseInt(gold, 10);
  const valid = Number.isInteger(value) && value >= 0 && value <= 100000;
  return (
    <section className="section" aria-labelledby="scenario-heading">
      <h4 id="scenario-heading">Cenário: e se eu tivesse outro ouro?</h4>
      <p className="muted">O cenário é separado do histórico: não muda o que aconteceu na partida.</p>
      <label htmlFor="scenario-gold">Ouro</label>
      <input
        id="scenario-gold"
        inputMode="numeric"
        value={gold}
        onChange={(event) => setGold(event.target.value)}
      />
      <div className="actions">
        <button
          type="button"
          className="button button--secondary"
          disabled={!valid}
          onClick={() =>
            api
              .createScenarioAnalysis(attempt.decisionId ?? "", comparison.objective.priority, value, crypto.randomUUID())
              .then(setResult)
              .catch(setError)
          }
        >
          Simular cenário
        </button>
      </div>
      {error ? <ErrorNotice error={error} /> : null}
      {result ? (
        <ul className="comparison-list">
          {(result.candidates ?? []).map((candidate) => (
            <li key={candidate.itemId}>
              Cenário com {value} de ouro — {name(candidate)}: faltam {candidate.remainingCost};{" "}
              {AFFORDABILITY_TEXT[candidate.affordability] ?? candidate.affordability}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

export function StudyPanel({ decisionId }: { decisionId: string }) {
  const [priority, setPriority] = useState<ObjectivePriority>("BALANCED");
  const [attempt, setAttempt] = useState<LearningAttempt | null>(null);
  const [choice, setChoice] = useState<number | "skip" | null>(null);
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function run(action: () => Promise<LearningAttempt>) {
    setBusy(true);
    setError(null);
    try {
      setAttempt(await action());
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  if (attempt === null) {
    return (
      <section className="section" aria-labelledby="study-heading">
        <h3 id="study-heading">Estudar esta decisão</h3>
        <p className="muted">Escolha o que você queria priorizar antes de ver as alternativas.</p>
        <fieldset className="field">
          <legend>Objetivo</legend>
          {OBJECTIVES.map((objective) => (
            <label key={objective.value} className="radio-option">
              <input
                type="radio"
                name="objective"
                value={objective.value}
                checked={priority === objective.value}
                onChange={() => setPriority(objective.value)}
              />
              {objective.label} <span className="muted">— {objective.hint}</span>
            </label>
          ))}
        </fieldset>
        <div className="actions">
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={() => run(() => api.createAttempt(decisionId, priority, crypto.randomUUID()))}
          >
            Confirmar objetivo e começar
          </button>
        </div>
        {error ? <ErrorNotice error={error} /> : null}
      </section>
    );
  }

  const revealed = attempt.status === "REVEALED";
  const answered = attempt.status !== "OPEN";
  return (
    <section className="section" aria-labelledby="study-heading">
      <h3 id="study-heading">Estudar esta decisão</h3>
      {attempt.options.length === 0 ? (
        <div className="notice notice--info" role="note">
          <p>Não há alternativas para estudar nesta decisão.</p>
          <ul>
            {(attempt.reasonCodes ?? []).map((code) => (
              <li key={code}>{reasonText(code)}</li>
            ))}
          </ul>
        </div>
      ) : (
        <fieldset className="field" disabled={answered || busy}>
          <legend>Qual destas você compraria? (ordem sem significado)</legend>
          {attempt.options.map((option) => (
            <label key={option.itemId} className="radio-option">
              <input
                type="radio"
                name="choice"
                checked={answered ? attempt.selectedItemId === option.itemId : choice === option.itemId}
                onChange={() => setChoice(option.itemId)}
              />
              {name(option)}
            </label>
          ))}
          <label className="radio-option">
            <input
              type="radio"
              name="choice"
              checked={answered ? attempt.skipped === true : choice === "skip"}
              onChange={() => setChoice("skip")}
            />
            Não sei
          </label>
          <label htmlFor="rationale">Por quê? (opcional)</label>
          <textarea
            id="rationale"
            maxLength={1000}
            value={answered ? (attempt.rationale ?? "") : rationale}
            onChange={(event) => setRationale(event.target.value)}
          />
        </fieldset>
      )}
      {(attempt.hints ?? []).length > 0 ? (
        <ul className="hint-text">
          {(attempt.hints ?? []).map((hint) => (
            <li key={hint.text}>
              Pista {hint.level}: {hint.text}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="actions">
        {!answered && attempt.options.length > 0 ? (
          <button
            type="button"
            className="button"
            disabled={choice === null || busy}
            onClick={() =>
              run(() =>
                api.answerAttempt(attempt.attemptId, {
                  itemId: choice === "skip" ? null : choice,
                  skipped: choice === "skip",
                  rationale: rationale.trim() || null,
                }),
              )
            }
          >
            Responder
          </button>
        ) : null}
        {!revealed && attempt.options.length > 0 && attempt.hintLevel < 1 ? (
          <button
            type="button"
            className="button button--secondary"
            disabled={busy}
            onClick={() => run(() => api.hintAttempt(attempt.attemptId, 1))}
          >
            Pista: observar ameaças
          </button>
        ) : null}
        {!revealed && attempt.options.length > 0 && attempt.hintLevel < 2 ? (
          <button
            type="button"
            className="button button--secondary"
            disabled={busy}
            onClick={() => run(() => api.hintAttempt(attempt.attemptId, 2))}
          >
            Pista: custo e atributos
          </button>
        ) : null}
        {!revealed ? (
          <button
            type="button"
            className="button button--secondary"
            disabled={busy}
            onClick={() => run(() => api.revealAttempt(attempt.attemptId))}
          >
            Revelar comparação
          </button>
        ) : null}
      </div>
      {error ? <ErrorNotice error={error} /> : null}
      {revealed ? <Comparison attempt={attempt} /> : null}
      {revealed ? <Scenario attempt={attempt} /> : null}
    </section>
  );
}
