import { useState, type FormEvent } from "react";
import { api, type AnalysisCandidate, type AnalysisLate, type ExplanationResult } from "../../shared/api/client";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { formatGameTime } from "../matches/format";

/**
 * Late game (CC-1504/1505): papéis de cada item na decisão, a explicação com fontes e, depois de
 * revelar, quando a escolha foi feita. Tudo vem do servidor; a tela só traduz.
 */

const percent = (value: number | null | undefined) =>
  value == null ? null : `${Math.round(value * 100)}%`;

/** "Padrão do campeão" e "esta partida pede", com a probabilidade e a do padrão. */
export function LateMarks({ candidate, late }: { candidate: AnalysisCandidate; late?: AnalysisLate | null }) {
  if (!late) return null;
  const now = percent(candidate.probability);
  const usual = percent(candidate.baseProbability);
  return (
    <p className="comparison-card__marks late-marks">
      {candidate.isDefault ? <span className="late-tag late-tag--default">padrão do campeão</span> : null}
      {candidate.isDeviation ? (
        <span className="late-tag late-tag--deviation">
          {late.claimStrong ? "mais comprado nesta situação" : "vale considerar nesta situação"}
        </span>
      ) : null}
      {now && usual ? (
        <span className="muted">
          {late.gatePassed ? `${now} nesta partida · ` : ""}
          {usual} no padrão
        </span>
      ) : null}
    </p>
  );
}

export function LateSummary({ late }: { late: AnalysisLate }) {
  return (
    <p className="muted" data-testid="late-summary">
      {late.ordinal}º item principal (botas fora da contagem) ·{" "}
      {late.gatePassed
        ? "modelo contextual aprovado no teste pré-registrado: a ordem considera a composição, os itens dos inimigos, o placar e quem matou você. Descreve o que jogadores compram nessa situação; não promete vitória"
        : "sem modelo contextual aprovado para este patch: a ordem é o padrão do campeão nesta etapa"}
      {late.sourcePatch ? ` · referência do patch ${late.sourcePatch}` : ""}
      {late.championSupport > 0 ? ` · ${late.championSupport} decisões deste campeão nesta etapa` : ""}.
    </p>
  );
}

/** Pergunte por quê: até 4 frases, cada uma com as fontes; os fatos ficam abertos para conferir. */
export function LateExplanation({ analysisId }: { analysisId: string }) {
  const [result, setResult] = useState<ExplanationResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [question, setQuestion] = useState("");

  async function ask(text: string | null) {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.explainAnalysis(analysisId, text));
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const text = question.trim();
    if (text) void ask(text);
  }

  const titles = new Map((result?.facts ?? []).map((fact) => [fact.id, fact.title]));
  return (
    <section className="section late-explanation" aria-labelledby="late-explanation-heading" aria-busy={busy}>
      <h4 id="late-explanation-heading">Por que esta recomendação?</h4>
      {!result ? (
        <button type="button" className="button button--secondary" disabled={busy} onClick={() => void ask(null)}>
          {busy ? "Buscando os fatos…" : "Explicar com as fontes"}
        </button>
      ) : (
        <>
          <ul className="late-explanation__answer">
            {(result.sentences ?? []).map((sentence) => (
              <li key={sentence.text}>
                {sentence.text}{" "}
                <span className="late-explanation__sources">
                  ({sentence.sources.map((id) => titles.get(id) ?? id).join("; ")})
                </span>
              </li>
            ))}
          </ul>
          <p className="muted">
            {result.provider === "LOCAL_MODEL"
              ? "Texto escrito pelo modelo local e conferido contra os fatos abaixo."
              : "Texto montado diretamente dos fatos abaixo."}
          </p>
          <details className="details">
            <summary>Fatos usados ({(result.facts ?? []).length})</summary>
            <ul>
              {(result.facts ?? []).map((fact) => (
                <li key={fact.id}>
                  <strong>{fact.title}:</strong> {fact.text}
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
      <form className="field late-explanation__form" onSubmit={submit}>
        <label htmlFor="late-question">Pergunte sobre esta decisão</label>
        <input
          id="late-question"
          type="text"
          maxLength={300}
          value={question}
          placeholder="Ex.: por que não um item de Resistência Mágica?"
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button type="submit" className="button" disabled={busy || !question.trim()}>
          Perguntar
        </button>
      </form>
      {error ? <ErrorNotice error={error} /> : null}
    </section>
  );
}

type Commitment = {
  kind?: string;
  sharePaid?: number | null;
  anchorTimestampMs?: number | null;
  completedAtMs?: number | null;
  pathStartItemName?: string | null;
  pathStartTimestampMs?: number | null;
};

const KIND_TEXT: Readonly<Record<string, string>> = {
  FORCED: "Quando o item ficou pronto, a compra já estava decidida: nenhum outro item aproveitava o ouro já pago.",
  SHARED: "Quando o item ficou pronto, ainda havia outro item que aproveitava os mesmos componentes.",
  OPEN: "O item foi comprado inteiro de uma vez, sem componentes pagos antes.",
};

/** Depois de revelar: quando a escolha foi feita e quanto já estava pago ao terminar o item. */
export function CommitmentNote({ commitment }: { commitment?: Record<string, unknown> | null }) {
  if (!commitment) return null;
  const c = commitment as Commitment;
  const paid = percent(c.sharePaid ?? null);
  return (
    <div className="notice notice--info late-commitment" role="note" data-testid="commitment-note">
      <p>
        <strong>Quando a escolha foi feita.</strong>{" "}
        {c.anchorTimestampMs != null ? `A decisão começa em ${formatGameTime(c.anchorTimestampMs)}` : "A decisão começa"}
        {c.pathStartItemName && c.pathStartTimestampMs != null
          ? `; o caminho começou com ${c.pathStartItemName} em ${formatGameTime(c.pathStartTimestampMs)}`
          : ""}
        {c.completedAtMs != null ? ` e o item ficou pronto em ${formatGameTime(c.completedAtMs)}` : ""}.
        {paid ? ` Nesse instante, ${paid} do preço já estava pago em componentes.` : ""}
      </p>
      {c.kind && KIND_TEXT[c.kind] ? <p>{KIND_TEXT[c.kind]}</p> : null}
    </div>
  );
}
