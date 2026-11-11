import { useEffect, useRef, useState } from "react";
import { api, reportUrl, type AnalysisResult } from "../../shared/api/client";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { useRouter } from "../../shared/router/context";
import { GlossaryDisclosure } from "../review/components/GlossaryDisclosure";
import { GameIcon } from "../../shared/ui/GameIcon";
import { Comparison, Reanalysis, Scenario, StudyPanel } from "./StudyPanel";

function DirectAnalysis({ decisionId }: { decisionId: string }) {
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [retry, setRetry] = useState(0);
  const [requestKey] = useState(() => crypto.randomUUID());
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]);
    api.createObjectiveAnalysis(decisionId, { priority: "BALANCED" }, requestKey, signal)
      .then((data) => { if (!controller.signal.aborted) setResult(data); })
      .catch((caught: unknown) => { if (!controller.signal.aborted) setError(caught); });
    return () => controller.abort();
  }, [decisionId, requestKey, retry]);
  return <section className="surface match-panel study-panel" aria-labelledby="direct-analysis-heading">
    <div className="study-panel__head">
      <h3 id="direct-analysis-heading">Análise da decisão</h3>
      <p>As alternativas para este momento, sem exercício. Objetivo inicial: equilíbrio.</p>
    </div>
    <details className="how-to-read">
      <summary>Como ler esta comparação</summary>
      <p className="notice notice--info" role="note">A ordem usa o critério de referência disponível, não uma garantia do melhor item. Popularidade e compra histórica não são gabaritos.</p>
      <GlossaryDisclosure />
    </details>
    {!result && !error ? <p role="status">Analisando as alternativas disponíveis…</p> : null}
    {error ? <ErrorNotice error={error} retryLabel="Tentar análise novamente" onRetry={() => { setError(null); setRetry((value) => value + 1); }} /> : null}
    {result ? <>
      <Comparison comparison={result} headingRef={heading} objectiveSource="padrão equilibrado da análise" />
      {/* E se...: outro ouro ou outro objetivo, cada um num cartão; a comparação acima não muda. */}
      <div className="what-if">
        <Reanalysis decisionId={decisionId} comparison={result} />
        <Scenario decisionId={decisionId} comparison={result} />
      </div>
      <div className="actions study-panel__downloads">
        <a className="button button--secondary" href={reportUrl(result.analysisId, "html")} download>Baixar análise HTML</a>
        <a className="button button--secondary" href={reportUrl(result.analysisId, "json")} download>Baixar análise JSON</a>
      </div>
    </> : null}
  </section>;
}

export function DecisionExperience({ decisionId }: { decisionId: string }) {
  const { href, navigate } = useRouter();
  const practice = new URLSearchParams(href.split("?")[1]).get("mode") === "practice";
  return <>
    {/* Dois caminhos como cartões: ver a análise ou praticar antes de ver. */}
    <div className="decision-mode" role="group" aria-label="Modo de revisão">
      <button type="button" className="decision-mode__card" aria-pressed={!practice} aria-describedby="mode-analyze-desc" onClick={() => navigate(`/decisions/${decisionId}`)}>
        <GameIcon name="chart" size={22} />
        <span className="decision-mode__title">Analisar decisão</span>
        <span id="mode-analyze-desc" className="decision-mode__desc" aria-hidden="true">Ver as alternativas e por que cada uma aparece.</span>
      </button>
      <button type="button" className="decision-mode__card" aria-pressed={practice} aria-describedby="mode-practice-desc" onClick={() => navigate(`/decisions/${decisionId}?mode=practice`)}>
        <GameIcon name="target" size={22} />
        <span className="decision-mode__title">Praticar decisão</span>
        <span id="mode-practice-desc" className="decision-mode__desc" aria-hidden="true">Escolher o item antes de ver a resposta, como um desafio.</span>
      </button>
    </div>
    {practice ? <>
      <p className="muted">Prática opcional: escolha antes de revelar. Se já viu a análise desta decisão, use outra para praticar sem influência. Trocar de modo reinicia a visualização; registros já salvos são preservados.</p>
      <StudyPanel key={decisionId} decisionId={decisionId} />
    </> : <DirectAnalysis key={decisionId} decisionId={decisionId} />}
  </>;
}
