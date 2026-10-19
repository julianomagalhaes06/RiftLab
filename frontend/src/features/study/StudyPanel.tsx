import { useEffect, useRef, useState, type RefObject } from "react";
import {
  api,
  type AnalysisCandidate,
  type AnalysisReferenceSource,
  type AnalysisResult,
  type LearningAttempt,
  type LearningProgress,
  type ObjectiveForm,
} from "../../shared/api/client";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { ObjectivePicker } from "../objectives/ObjectivePicker";
import { ExportLinks, Reflection, ReviewerFeedback } from "../reports/AfterComparison";
import { objectiveLabel, objectiveOrigin } from "../objectives/objectiveText";
import { GlossaryDisclosure } from "../review/components/GlossaryDisclosure";
import { useItemName } from "../../shared/hooks/useItemName";
import { ItemIcon } from "../../shared/ui/ItemIcon";
import "../review/review.css";

/**
 * Jornada de estudo de uma decisão (CC-304/305, docs/learning-design.md): escolher o
 * objetivo, escolher entre alternativas em ordem neutra (ou "não sei"), consultar pistas
 * e só então revelar a comparação. Antes de revelar não há posição, pontuação, custo nem
 * compra histórica na tela.
 */

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
  INHERITED_RULES_SUSPENDED: "Herança suspensa por divergências no inventário final deste patch.",
  INHERITED_RULES_UNVERIFIED: "Ainda sem amostra suficiente para conferir as regras herdadas neste patch.",
  RECONSTRUCTION_RULES_INHERITED:
    "O inventário usa regras de reconstrução confirmadas num patch anterior, herdadas porque este patch ainda não tem confirmação própria nesta fila.",
  ITEM_RULES_INHERITED:
    "Os limites de itens (exclusividade) vêm do último patch revisado; itens que ele não cobre ficam fora das alternativas.",
  RULES_MEASURED_IN_SOLO_DUO:
    "As regras de inventário foram medidas em Solo/Duo; o Flex ainda não tem amostra suficiente para conferi-las.",
  POPULARITY_INHERITED: "As contagens de referência foram herdadas de um patch anterior e filtradas pelo catálogo desta decisão.",
  POPULARITY_SOURCE_CATALOG_IDENTITY_UNBOUND: "A referência anterior não identifica exatamente o catálogo de origem; foi usado o critério de regras explícitas.",
  POPULARITY_SOURCE_CATALOG_IDENTITY_INVALID: "A identidade do catálogo de origem da referência anterior é inválida; foi usado o critério de regras explícitas.",
  POPULARITY_SOURCE_CATALOG_NOT_FOUND: "O catálogo exato da referência anterior não está disponível; foi usado o critério de regras explícitas.",
  POPULARITY_SOURCE_CATALOG_IDENTITY_MISMATCH: "O catálogo não corresponde à identidade congelada da referência; foi usado o critério de regras explícitas.",
  POPULARITY_SOURCE_PATCH_NOT_PREVIOUS: "A referência não pertence a um patch numérico anterior; foi usado o critério de regras explícitas.",
  NO_INHERITED_POPULARITY_FOR_CELL: "Após o filtro do catálogo não restaram decisões de referência para esta célula; foi usado o critério de regras explícitas.",
};

function engineText(engine: string, referenceSource?: AnalysisReferenceSource | null): string {
  // Na herança, as decisões de referência são do patch de origem, não do patch da partida.
  if (engine === "POPULARITY" && referenceSource?.type === "INHERITED") {
    return `Popularidade nas decisões de referência do patch ${referenceSource.patch} deste campeão nesta etapa da build`;
  }
  return ENGINE_TEXT[engine] ?? engine;
}

function reasonText(code: string): string {
  return REASON_TEXT[code] ?? `Motivo: ${code}`;
}

export function Comparison({ comparison, selectedItemId, historicalItemId, headingRef, objectiveSource }: {
  comparison: AnalysisResult | null;
  selectedItemId?: number | null;
  historicalItemId?: number | null;
  headingRef: RefObject<HTMLHeadingElement | null>;
  objectiveSource?: string;
}) {
  const name = useItemName();
  if (!comparison) {
    return null;
  }
  const candidates: AnalysisCandidate[] = comparison.candidates ?? [];
  const reasons = comparison.support?.reasonCodes ?? [];
  const referenceSource = comparison.support?.referenceSource;
  return (
    <section className="section" aria-labelledby="comparison-heading">
      <h3 id="comparison-heading" ref={headingRef} tabIndex={-1}>
        Comparação
      </h3>
      <p className="muted">
        Objetivo: {objectiveLabel(comparison.objective.priority)} ({objectiveSource ?? objectiveOrigin(comparison.objective)}) ·
        Critério: {engineText(comparison.engine, referenceSource)}
      </p>
      {referenceSource ? (
        <p className="muted" data-testid="reference-source">
          {referenceSource.type === "INHERITED"
            ? `Referência da popularidade: patch ${referenceSource.patch} (herdada; filtrada pelo catálogo desta decisão).`
            : referenceSource.type === "OWN"
              ? `Referência da popularidade: patch ${referenceSource.patch} (própria).`
              : `Referência da popularidade do patch ${referenceSource.patch} não foi usada.`}
        </p>
      ) : null}
      {comparison.engine === "RULES" && referenceSource?.type === "UNAVAILABLE" ? (
        <ul className="muted" aria-label="Motivo da referência indisponível">
          {reasons.map((code) => <li key={code}>{reasonText(code)}</li>)}
        </ul>
      ) : null}
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
              const chosen = selectedItemId === candidate.itemId;
              const historical = historicalItemId === candidate.itemId;
              const moved =
                candidate.baseRank != null && candidate.baseRank !== candidate.rank
                  ? candidate.baseRank > candidate.rank
                    ? "subiu pela prioridade do objetivo"
                    : "desceu pela prioridade do objetivo"
                  : null;
              return (
                <li key={candidate.itemId} className={chosen ? "comparison-list__item--chosen" : undefined}>
                  <strong>
                    <span className="item-with-icon"><ItemIcon itemId={candidate.itemId} /><span>{candidate.rank}. {name(candidate)}</span></span>
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
      {historicalItemId != null &&
      !candidates.some((candidate) => candidate.itemId === historicalItemId) ? (
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
          {comparison.versions.registryFallback === "ACTIVE_ARTIFACT_HASH_MISMATCH" ? (
            <li>
              Registro de modelos: a versão ativa foi recusada porque o arquivo não confere com o
              hash registrado; a comparação usa o critério explícito acima.
            </li>
          ) : null}
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

export function Scenario({ decisionId, comparison }: { decisionId: string; comparison: AnalysisResult | null }) {
  const name = useItemName();
  const [gold, setGold] = useState("");
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  if (!comparison || (comparison.candidates ?? []).length === 0) {
    return null;
  }
  const value = Number.parseInt(gold, 10);
  const valid = Number.isInteger(value) && value >= 0 && value <= 100000;
  return (
    <section className="section" aria-labelledby="scenario-heading">
      <h4 id="scenario-heading">Cenário: e se eu tivesse outro ouro?</h4>
      <p className="muted">O cenário é separado do histórico: não muda o que aconteceu na partida.</p>
      <div className="field">
        <label htmlFor="scenario-gold">Ouro</label>
        <input
          id="scenario-gold"
          className="form__input"
          inputMode="numeric"
          value={gold}
          onChange={(event) => setGold(event.target.value)}
        />
      </div>
      <div className="actions">
        <button
          type="button"
          className="button button--secondary"
          disabled={!valid}
          onClick={() =>
            api
              .createScenarioAnalysis(decisionId, comparison.objective, value, crypto.randomUUID())
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
              Cenário com {value} de ouro — <span className="item-with-icon"><ItemIcon itemId={candidate.itemId} /><span>{name(candidate)}</span></span>: faltam {candidate.remainingCost};{" "}
              {AFFORDABILITY_TEXT[candidate.affordability] ?? candidate.affordability}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/**
 * Reanalisar com outro objetivo (UC03): a nova análise aparece separada e a comparação
 * anterior continua na tela; falha ou timeout da interpretação não a apagam.
 */
export function Reanalysis({ decisionId, comparison }: { decisionId: string; comparison: AnalysisResult | null }) {
  const name = useItemName();
  const [objective, setObjective] = useState<ObjectiveForm>(
    comparison?.objective ?? { priority: "BALANCED" },
  );
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (!comparison || (comparison.candidates ?? []).length === 0) {
    return null;
  }
  return (
    <section className="section" aria-labelledby="reanalysis-heading">
      <h4 id="reanalysis-heading">Reanalisar com outro objetivo</h4>
      <p className="muted">
        A nova análise fica separada; a comparação acima não muda. O objetivo só reordena as
        alternativas legais pelos atributos do catálogo.
      </p>
      <ObjectivePicker idPrefix="reanalysis" value={objective} onChange={setObjective} />
      <div className="actions">
        <button
          type="button"
          className="button button--secondary"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            setError(null);
            api
              .createObjectiveAnalysis(decisionId, objective, crypto.randomUUID())
              .then(setResult)
              .catch(setError)
              .finally(() => setBusy(false));
          }}
        >
          Reanalisar
        </button>
      </div>
      {error ? <ErrorNotice error={error} /> : null}
      {result ? (
        <>
          <p className="muted">
            Nova análise — objetivo {objectiveLabel(result.objective.priority)} (
            {objectiveOrigin(result.objective)}):
          </p>
          <ol className="comparison-list">
            {(result.candidates ?? []).map((candidate) => (
              <li key={candidate.itemId}>
                <span className="item-with-icon"><ItemIcon itemId={candidate.itemId} /><span>{candidate.rank}. {name(candidate)}</span></span>
              </li>
            ))}
          </ol>
        </>
      ) : null}
    </section>
  );
}

/** Progresso local descritivo (CC-505): contagens, sem nota nem estimativa de habilidade. */
function Progress() {
  const [progress, setProgress] = useState<LearningProgress | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api
      .getProgress(controller.signal)
      .then(setProgress)
      .catch(() => setProgress(null));
    return () => controller.abort();
  }, []);
  if (!progress || progress.study.attempts + progress.transfer.attempts === 0) {
    return null;
  }
  return (
    <p className="muted">
      Neste computador: {progress.study.attempts} estudo(s), {progress.study.reflections} com
      reflexão, {progress.study.usedHints} com pistas; {progress.transfer.attempts} prática(s) sem
      pistas. São só contagens: não medem habilidade.
    </p>
  );
}

export function StudyPanel({ decisionId }: { decisionId: string }) {
  const name = useItemName();
  const [objective, setObjective] = useState<ObjectiveForm>({ priority: "BALANCED" });
  const [transfer, setTransfer] = useState(false);
  const [attempt, setAttempt] = useState<LearningAttempt | null>(null);
  const [choice, setChoice] = useState<number | "skip" | null>(null);
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // O botão clicado some quando a tentativa avança; o foco vai para o próximo passo em vez
  // de cair no topo da página (teclado e leitor de tela).
  const studyHeading = useRef<HTMLHeadingElement>(null);
  const choiceLegend = useRef<HTMLLegendElement>(null);
  const hintList = useRef<HTMLUListElement>(null);
  const revealButton = useRef<HTMLButtonElement>(null);
  const comparisonHeading = useRef<HTMLHeadingElement>(null);
  const previous = useRef<LearningAttempt | null>(null);

  useEffect(() => {
    const before = previous.current;
    previous.current = attempt;
    if (attempt === null || before === attempt) {
      return;
    }
    if (before === null) {
      (choiceLegend.current ?? studyHeading.current)?.focus();
    } else if (attempt.status === "REVEALED" && before.status !== "REVEALED") {
      comparisonHeading.current?.focus();
    } else if (attempt.hintLevel > before.hintLevel) {
      hintList.current?.focus();
    } else if (attempt.status === "ANSWERED" && before.status === "OPEN") {
      revealButton.current?.focus();
    }
  }, [attempt]);

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
        {!transfer ? <GlossaryDisclosure /> : null}
        <ObjectivePicker idPrefix="study" value={objective} onChange={setObjective} />
        <label className="radio-option">
          <input type="checkbox" checked={transfer} onChange={(event) => setTransfer(event.target.checked)} />
          Praticar sem pistas (transferência): use numa decisão diferente da que você estudou.
        </label>
        <Progress />
        <div className="actions">
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={() => run(() =>
                api.createAttempt(
                  decisionId,
                  objective,
                  crypto.randomUUID(),
                  undefined,
                  transfer ? "TRANSFER" : "STUDY",
                ),
              )}
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
      <h3 id="study-heading" ref={studyHeading} tabIndex={-1}>
        Estudar esta decisão
      </h3>
      {attempt.mode === "TRANSFER" ? <p className="muted">Modo sem pistas (transferência).</p> : null}
      {attempt.mode !== "TRANSFER" ? <GlossaryDisclosure /> : null}
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
          <legend ref={choiceLegend} tabIndex={-1}>
            Qual destas você compraria? (ordem sem significado)
          </legend>
          {attempt.options.map((option) => (
            <label key={option.itemId} className="radio-option">
              <input
                type="radio"
                name="choice"
                checked={answered ? attempt.selectedItemId === option.itemId : choice === option.itemId}
                onChange={() => setChoice(option.itemId)}
              />
              <span className="item-with-icon"><ItemIcon itemId={option.itemId} /><span>{name(option)}</span></span>
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
        <ul className="hint-text" ref={hintList} tabIndex={-1} aria-label="Pistas abertas">
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
        {!revealed && attempt.mode !== "TRANSFER" && attempt.options.length > 0 && attempt.hintLevel < 1 ? (
          <button
            type="button"
            className="button button--secondary"
            disabled={busy}
            onClick={() => run(() => api.hintAttempt(attempt.attemptId, 1))}
          >
            Pista: observar ameaças
          </button>
        ) : null}
        {!revealed && attempt.mode !== "TRANSFER" && attempt.options.length > 0 && attempt.hintLevel < 2 ? (
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
            ref={revealButton}
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
      {revealed ? <Comparison comparison={attempt.comparison ?? null} selectedItemId={attempt.selectedItemId} historicalItemId={attempt.historicalItemId} headingRef={comparisonHeading} /> : null}
      {revealed ? <Scenario decisionId={decisionId} comparison={attempt.comparison ?? null} /> : null}
      {revealed ? <Reanalysis decisionId={decisionId} comparison={attempt.comparison ?? null} /> : null}
      {revealed ? <Reflection attempt={attempt} onSaved={setAttempt} /> : null}
      {revealed ? <ExportLinks attempt={attempt} /> : null}
      {revealed ? <ReviewerFeedback attempt={attempt} /> : null}
    </section>
  );
}
