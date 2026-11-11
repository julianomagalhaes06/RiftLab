import { useCallback, useEffect, useState, type KeyboardEvent, type ReactNode } from "react";
import { api, type DecisionSummary, type MatchDetail, type MatchParticipant, type MatchTimeline } from "../../shared/api/client";
import { isAbortError, isApiError } from "../../shared/api/errors";
import { useAbortScope } from "../../shared/hooks/useAbortScope";
import { usePolling } from "../../shared/hooks/usePolling";
import { Link } from "../../shared/router/Link";
import { useRouter } from "../../shared/router/context";
import { useAuth } from "../auth/authContext";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { usePagedList } from "../../shared/hooks/usePagedList";
import { ItemNames } from "../../shared/ui/ItemNames";
import { ChampionAvatar } from "../history/ChampionAvatar";
import { championWithRole } from "../history/presentation";
import { BuildTimeline, DecisionChip, SkillOrder } from "./BuildTimeline";
import { championName, reasonText } from "./format";
import { GoldChart } from "./GoldChart";
import { MatchHeader } from "./MatchHeader";
import { MatchReviewPanel } from "./MatchReviewPanel";
import { summarizeDecisions } from "./matchView";
import { PreparationPanel } from "./PreparationPanel";
import { PurgePanel } from "./PurgePanel";
import { Scoreboard } from "./Scoreboard";
import { MatchStudyBar } from "../study/Gamification";
import "./match.css";
import { count } from "../../shared/text/count";
import { useDemoNotice } from "../../app/demoNotice";
import { isDemoMatchId } from "../../shared/router/routes";

/** Preparação em andamento (PENDING/RUNNING) é o único estado não terminal. */
function preparationSettled(match: MatchDetail): boolean {
  const status = match.preparation.status;
  return status === "READY" || status === "FAILED" || status === "NOT_REQUESTED";
}

/** Linha do tempo da partida; recarrega quando a preparação termina (ouro por minuto). */
function useMatchTimeline(matchId: string, readyKey: string) {
  const [state, setState] = useState<{ key: string; timeline: MatchTimeline | null; failed: boolean } | null>(null);
  const key = `${matchId}#${readyKey}`;
  useEffect(() => {
    const controller = new AbortController();
    api
      .getMatchTimeline(matchId, controller.signal)
      .then((timeline) => setState({ key, timeline, failed: false }))
      .catch((error: unknown) => {
        if (!isAbortError(error)) setState({ key, timeline: null, failed: true });
      });
    return () => controller.abort();
  }, [matchId, key]);
  return state?.key === key ? state : null;
}

function DecisionsSummary({ decisions }: { decisions: DecisionSummary[] }) {
  const summary = summarizeDecisions(decisions);
  if (!summary.total) return null;
  const blocked = summary.total - summary.eligible;
  return (
    <div className={`decisions-summary${summary.eligible ? "" : " decisions-summary--blocked"}`} role="note">
      <p className="decisions-summary__headline">
        {summary.eligible === 0
          ? summary.total === 1
            ? "A decisão de compra desta partida não pode ser analisada."
            : `Nenhuma das ${summary.total} decisões de compra desta partida pode ser analisada.`
          : `${summary.eligible} de ${count(summary.total, "decisão de compra", "decisões de compra")} ${summary.eligible === 1 ? "pode ser analisada" : "podem ser analisadas"}.`}
      </p>
      {summary.eligible === 0 ? (
        // CC-904: sem beco sem saída; o que a partida ainda oferece.
        <p className="decisions-summary__next">
          O trajeto de compras e a ordem de habilidades continuam aqui; o gráfico de ouro fica na aba Ouro.
        </p>
      ) : null}
      {blocked > 0 && summary.reasons.length ? (
        <ul className="decisions-summary__reasons">
          {summary.reasons.map(([code, count]) => (
            <li key={code}>
              {reasonText(code)} <span className="muted">({count === blocked ? "todas as bloqueadas" : `${count} de ${blocked}`})</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function ParticipantSection({
  matchId,
  participant,
  timeline,
  local,
  onDecisions,
}: {
  matchId: string;
  participant: MatchParticipant;
  timeline: MatchTimeline | null;
  local: boolean;
  /** Repassa a lista já carregada (barra de estudo da partida, sem nova busca). */
  onDecisions?: (items: DecisionSummary[]) => void;
}) {
  const participantId = participant.participantId;
  const list = usePagedList<DecisionSummary>({
    resetKey: `${matchId}:${participantId}`,
    fetchPage: (cursor, signal) => api.listDecisions(matchId, participantId, { cursor, limit: 20 }, signal),
  });
  const cursorExpired = isApiError(list.loadMoreError) && list.loadMoreError.code === "INVALID_CURSOR";
  useEffect(() => {
    if (list.status === "ready") onDecisions?.(list.items);
  }, [list.status, list.items, onDecisions]);
  const ownTimeline = timeline?.participants.find((p) => p.participantId === participantId) ?? null;
  const owner = championWithRole(championName(participant), participant.role);
  return (
    <section aria-labelledby="decisions-heading" className="surface match-panel">
      <div className="match-panel__heading">
        <ChampionAvatar participant={participant} size={40} />
        <div>
          <h3 id="decisions-heading">{local ? `Suas decisões — ${owner}` : `Decisões de ${owner}`}</h3>
          <p className="muted">Compras na ordem do tempo, agrupadas por volta à loja</p>
        </div>
      </div>
      {list.status === "loading" ? <p className="muted" aria-busy="true">Carregando decisões…</p> : null}
      {list.status === "error" ? <ErrorNotice error={list.error} onRetry={list.reload} retryLabel="Recarregar decisões" /> : null}
      {list.status === "ready" && list.items.length === 0 ? (
        <p className="muted">Nenhuma decisão de compra foi encontrada para este participante nesta partida.</p>
      ) : null}
      <DecisionsSummary decisions={list.items} />
      {timeline ? (
        <BuildTimeline participant={ownTimeline} decisions={list.items} />
      ) : list.items.length ? (
        <div className="build-path__orphans">
          {list.items.map((decision) => <DecisionChip key={decision.decisionId} decision={decision} />)}
        </div>
      ) : null}
      {list.loadMoreError ? (
        <ErrorNotice error={list.loadMoreError} onRetry={cursorExpired ? list.reload : list.loadMore} retryLabel={cursorExpired ? "Recarregar decisões" : "Tentar carregar mais"} />
      ) : null}
      {list.nextCursor !== null && !list.loadMoreError ? (
        <button type="button" className="button button--secondary" onClick={list.loadMore} disabled={list.loadingMore}>
          {list.loadingMore ? "Carregando…" : "Carregar mais decisões"}
        </button>
      ) : null}
      {timeline ? (
        <div className="match-panel__sub">
          <h4>Ordem de habilidades</h4>
          <SkillOrder participant={ownTimeline} />
        </div>
      ) : null}
    </section>
  );
}

type TabKey = "revisao" | "compras" | "placar" | "ouro";
const TAB_LABEL: Record<TabKey, string> = { revisao: "Revisão", compras: "Compras", placar: "Placar", ouro: "Ouro" };

/** Abas da análise (padrão WAI-ARIA: setas trocam de aba, a aba ativa recebe o foco). Trocam a
 * área de baixo sem rolar a página; a escolhida fica no endereço (?aba=) para voltar e compartilhar. */
function AnalysisTabs({ tabs, active, onChange }: { tabs: TabKey[]; active: TabKey; onChange(tab: TabKey): void }) {
  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const index = tabs.indexOf(active);
    const next = event.key === "ArrowRight" ? tabs[(index + 1) % tabs.length]
      : event.key === "ArrowLeft" ? tabs[(index - 1 + tabs.length) % tabs.length]
      : event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[tabs.length - 1] : null;
    if (!next) return;
    event.preventDefault();
    onChange(next);
    document.getElementById(`analysis-tab-${next}`)?.focus();
  }
  return (
    <div className="analysis-tabs" role="tablist" aria-label="Seções da análise">
      {tabs.map((tab) => (
        <button key={tab} id={`analysis-tab-${tab}`} type="button" role="tab" aria-selected={tab === active}
          aria-controls={`analysis-panel-${tab}`} tabIndex={tab === active ? 0 : -1}
          onClick={() => onChange(tab)} onKeyDown={onKeyDown}>
          {TAB_LABEL[tab]}
        </button>
      ))}
    </div>
  );
}

function TabPanel({ tab, active, children }: { tab: TabKey; active: TabKey; children: ReactNode }) {
  return (
    <div id={`analysis-panel-${tab}`} role="tabpanel" aria-labelledby={`analysis-tab-${tab}`} hidden={tab !== active} className="analysis-panel">
      {children}
    </div>
  );
}

/** Partida (/matches/:matchId?participant=N): preparação, participantes e decisões. */
export function MatchPage({
  matchId,
  participantId,
  fromHistory = false,
}: {
  matchId: string;
  participantId: number | null;
  fromHistory?: boolean;
}) {
  const { hasAccount } = useAuth();
  const { href, navigate } = useRouter();
  const newController = useAbortScope();
  const [preparing, setPreparing] = useState(false);
  useDemoNotice(isDemoMatchId(matchId));
  const [prepareError, setPrepareError] = useState<unknown>(null);

  const polling = usePolling<MatchDetail>({
    resetKey: matchId,
    fetcher: (signal) => api.getMatch(matchId, signal),
    isTerminal: preparationSettled,
    getRetryAt: (match) => match.preparation.retryAt,
  });
  const match = polling.data;

  async function handlePrepare() {
    setPrepareError(null);
    setPreparing(true);
    const controller = newController();
    try {
      await api.prepareMatch(matchId, controller.signal);
      polling.restart();
    } catch (error) {
      if (!isAbortError(error)) {
        setPrepareError(error);
      }
    } finally {
      setPreparing(false);
    }
  }

  const notFound =
    polling.phase === "failed" && isApiError(polling.error) && polling.error.status === 404;
  const localId = match?.localParticipantId ?? null;
  const selectedId = participantId ?? localId;
  const selectedParticipant =
    match && selectedId !== null
      ? (match.participants.find((p) => p.participantId === selectedId) ?? null)
      : null;
  const timelineState = useMatchTimeline(matchId, match?.preparation.status ?? "loading");
  const [studyDecisions, setStudyDecisions] = useState<{ key: string; items: DecisionSummary[] } | null>(null);
  const studyKey = `${matchId}#${selectedId ?? ""}`;
  const reportDecisions = useCallback((items: DecisionSummary[]) => setStudyDecisions({ key: studyKey, items }), [studyKey]);
  const ready = match?.preparation.status === "READY";
  // Abas só com a partida preparada; antes disso a página mostra o placar, como sempre.
  const tabs: TabKey[] = !ready ? [] : [
    ...(selectedParticipant ? (["revisao", "compras"] as const) : []),
    "placar",
    ...(timelineState?.timeline ? (["ouro"] as const) : []),
  ];
  const requested = new URLSearchParams(href.split("?")[1]).get("aba") as TabKey | null;
  const activeTab: TabKey = requested && tabs.includes(requested) ? requested : tabs[0] ?? "placar";
  function changeTab(tab: TabKey) {
    const [path, query = ""] = href.split("?");
    const params = new URLSearchParams(query);
    params.set("aba", tab);
    navigate(`${path}?${params.toString()}`, { replace: true });
  }

  return (
    <section className="area-page match-page" aria-labelledby="match-heading">
      {fromHistory ? <Link to="/historico" className="match-back">Voltar ao Histórico</Link> : null}
      {match === null ? <h2 id="match-heading">Partida {matchId}</h2> : null}

      {match === null && polling.phase === "polling" ? (
        <p className="muted" aria-busy="true">
          Carregando a partida…
        </p>
      ) : null}

      {polling.phase === "failed" ? (
        <ErrorNotice
          error={polling.error}
          onRetry={notFound ? undefined : polling.restart}
          retryLabel="Consultar novamente"
        />
      ) : null}

      {polling.phase === "polling" && polling.error !== null && match !== null ? (
        <p className="muted">Sem resposta da API; tentando de novo…</p>
      ) : null}

      {polling.phase === "stalled" ? (
        <div className="notice notice--info">
          <p>
            O acompanhamento automático parou depois de muito tempo. A preparação segue no servidor.
          </p>
          <button type="button" className="button button--secondary" onClick={polling.restart}>
            Continuar acompanhando
          </button>
        </div>
      ) : null}

      {match !== null ? (
        <ItemNames version={timelineState?.timeline?.dataDragonVersion}>
          <MatchHeader match={match} timeline={timelineState?.timeline ?? null} localId={localId} />

          <PreparationPanel
            preparation={match.preparation}
            busy={preparing}
            onPrepare={() => void handlePrepare()}
            requestError={prepareError}
          />

          {(() => {
            const scoreboard = (
              <section aria-labelledby="participants-heading" className="surface match-panel">
                <h3 id="participants-heading">Placar</h3>
                <Scoreboard
                  match={match}
                  timeline={timelineState?.timeline ?? null}
                  selected={selectedId}
                  localId={localId}
                  fromHistory={fromHistory}
                  stayOnScoreboard={ready}
                />
              </section>
            );
            // Sem preparação pronta: só o placar, que vem da importação.
            if (!ready) return scoreboard;
            return (
              <>
                {selectedParticipant && hasAccount ? <MatchStudyBar matchId={match.matchId} decisions={studyDecisions?.key === studyKey ? studyDecisions.items : null} /> : null}
                <AnalysisTabs tabs={tabs} active={activeTab} onChange={changeTab} />
                {selectedId !== null && selectedParticipant === null ? (
                  <div className="notice notice--info" role="note">
                    <p>O participante {selectedId} não existe nesta partida.</p>
                  </div>
                ) : null}
                {selectedParticipant ? (
                  <>
                    <TabPanel tab="revisao" active={activeTab}>
                      <MatchReviewPanel
                        key={`review-${selectedId}`}
                        matchId={match.matchId}
                        participant={selectedParticipant}
                        participants={match.participants}
                      />
                    </TabPanel>
                    <TabPanel tab="compras" active={activeTab}>
                      <ParticipantSection
                        key={selectedId}
                        matchId={match.matchId}
                        participant={selectedParticipant}
                        timeline={timelineState?.timeline ?? null}
                        local={selectedId === localId}
                        onDecisions={reportDecisions}
                      />
                    </TabPanel>
                  </>
                ) : null}
                <TabPanel tab="placar" active={activeTab}>
                  {selectedId === null ? <p className="muted">Selecione um participante no placar para ver a revisão, as compras e as decisões dele.</p> : null}
                  {scoreboard}
                </TabPanel>
                {timelineState?.timeline ? (
                  <TabPanel tab="ouro" active={activeTab}>
                    <section aria-labelledby="gold-heading" className="surface match-panel">
                      <h3 id="gold-heading">Ouro ao longo da partida</h3>
                      <GoldChart timeline={timelineState.timeline} participants={match.participants} />
                      {timelineState.timeline.goldFrames == null ? (
                        <button type="button" className="button button--secondary" onClick={() => void handlePrepare()} disabled={preparing}>
                          {preparing ? "Enviando…" : "Preparar de novo para gerar o gráfico"}
                        </button>
                      ) : null}
                    </section>
                  </TabPanel>
                ) : timelineState?.failed ? (
                  <p className="muted">A linha do tempo da partida não pôde ser carregada agora.</p>
                ) : null}
              </>
            );
          })()}

          {/* Remover apaga dado compartilhado: só com conta. */}
          {hasAccount ? (
            <details className="surface match-more">
              <summary>Remover esta partida do RiftLab</summary>
              <PurgePanel key={match.matchId} matchId={match.matchId} />
            </details>
          ) : null}
        </ItemNames>
      ) : null}

      {!fromHistory ? <p><Link to="/">Voltar ao início</Link></p> : null}
    </section>
  );
}
