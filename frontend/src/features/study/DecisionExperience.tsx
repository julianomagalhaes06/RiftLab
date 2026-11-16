import { useEffect, useRef, useState } from "react";
import { api, reportUrl, type AnalysisResult } from "../../shared/api/client";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { useRouter } from "../../shared/router/context";
import { GlossaryDisclosure } from "../review/components/GlossaryDisclosure";
import { GameIcon } from "../../shared/ui/GameIcon";
import { useSlowWait } from "../../shared/hooks/useSlowWait";
import { Comparison, Reanalysis, Scenario, StudyPanel } from "./StudyPanel";

/** Ao trocar de momento, a comparação anterior fica até a nova chegar (sem esvaziar o painel) e só
 * esmaece numa espera longa. Cada visita a um momento usa uma chave de idempotência nova; tentar de
 * novo reaproveita a da visita. */
function DirectAnalysis({ decisionId }: { decisionId: string }) {
  const [loaded, setLoaded] = useState<{ decisionId: string; result: AnalysisResult } | null>(null);
  const [failure, setFailure] = useState<{ decisionId: string; error: unknown } | null>(null);
  const [retry, setRetry] = useState(0);
  const visit = useRef<{ decisionId: string; requestKey: string } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (visit.current?.decisionId !== decisionId) visit.current = { decisionId, requestKey: crypto.randomUUID() };
    const { requestKey } = visit.current;
    const controller = new AbortController();
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]);
    api.createObjectiveAnalysis(decisionId, { priority: "BALANCED" }, requestKey, signal)
      .then((data) => { if (!controller.signal.aborted) setLoaded({ decisionId, result: data }); })
      .catch((caught: unknown) => { if (!controller.signal.aborted) setFailure({ decisionId, error: caught }); });
    return () => controller.abort();
  }, [decisionId, retry]);
  const current = loaded?.decisionId === decisionId ? loaded.result : null;
  const error = failure?.decisionId === decisionId ? failure.error : null;
  const stale = !current && !error ? loaded?.result ?? null : null;
  const result = current ?? stale;
  const slow = useSlowWait(stale !== null, decisionId);
  return <section className="surface match-panel study-panel" aria-labelledby="direct-analysis-heading" aria-busy={!current && !error} data-waiting={slow}>
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
    {stale ? <p role="status" className="sr-only">Analisando as alternativas deste momento…</p> : null}
    {error ? <ErrorNotice error={error} retryLabel="Tentar análise novamente" onRetry={() => { setFailure(null); setRetry((value) => value + 1); }} /> : null}
    {result && !error ? <>
      <Comparison comparison={result} headingRef={heading} objectiveSource="padrão equilibrado da análise" />
      {/* E se...: outro ouro ou outro objetivo, cada um num cartão; a comparação acima não muda.
          Os cartões são da análise mostrada: trocar de momento os zera (key). */}
      <div className="what-if" key={result.analysisId}>
        <Reanalysis decisionId={result.decisionId} comparison={result} />
        <Scenario decisionId={result.decisionId} comparison={result} />
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
    </> : <DirectAnalysis decisionId={decisionId} />}
  </>;
}
