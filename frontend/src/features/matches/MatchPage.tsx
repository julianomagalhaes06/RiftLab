import { useState } from "react";
import { api, type MatchDetail, type MatchParticipant } from "../../shared/api/client";
import { isAbortError, isApiError } from "../../shared/api/errors";
import { useAbortScope } from "../../shared/hooks/useAbortScope";
import { usePolling } from "../../shared/hooks/usePolling";
import { Link } from "../../shared/router/Link";
import { isDemoMatchId } from "../../shared/router/routes";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { DecisionList } from "./DecisionList";
import { PreparationPanel } from "./PreparationPanel";
import { ChampionBadge } from "./ChampionBadge";
import { roleLabel, teamLabel } from "./format";

/** Preparação em andamento (PENDING/RUNNING) é o único estado não terminal. */
function preparationSettled(match: MatchDetail): boolean {
  const status = match.preparation.status;
  return status === "READY" || status === "FAILED" || status === "NOT_REQUESTED";
}

function groupByTeam(participants: MatchParticipant[]): Array<[number, MatchParticipant[]]> {
  const teams = new Map<number, MatchParticipant[]>();
  for (const participant of participants) {
    teams.set(participant.team, [...(teams.get(participant.team) ?? []), participant]);
  }
  return [...teams.entries()].sort(([a], [b]) => a - b);
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? "data desconhecida"
    : date.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

function Participants({
  match,
  selected,
  fromHistory,
}: {
  match: MatchDetail;
  selected: number | null;
  fromHistory: boolean;
}) {
  return (
    <section aria-labelledby="participants-heading" className="section">
      <h3 id="participants-heading">Participantes</h3>
      {match.participants.length === 0 ? (
        <p className="muted">Esta partida não tem participantes registrados.</p>
      ) : (
        groupByTeam(match.participants).map(([team, members]) => (
          <div key={team} className="team">
            <h4 className="team__title">{teamLabel(team)}</h4>
            <ul className="list list--compact">
              {members.map((participant) => {
                const isSelected = participant.participantId === selected;
                return (
                  <li key={participant.participantId} className="list__item">
                    <Link
                      to={{
                        name: fromHistory ? "historyMatch" : "match",
                        matchId: match.matchId,
                        participantId: participant.participantId,
                      }}
                      className={`list__link${isSelected ? " list__link--selected" : ""}`}
                      aria-current={isSelected ? "true" : undefined}
                    >
                      <span className="list__title">
                        Participante {participant.participantId} —{" "}
                        <ChampionBadge participant={participant} />
                      </span>
                    </Link>
                    <span className="list__meta">{roleLabel(participant.role)}</span>
                  </li>
                );
              })}
            </ul>
          </div>
        ))
      )}
    </section>
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
  const newController = useAbortScope();
  const [preparing, setPreparing] = useState(false);
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
  const selectedParticipant =
    match && participantId !== null
      ? (match.participants.find((p) => p.participantId === participantId) ?? null)
      : null;

  return (
    <section className="section" aria-labelledby="match-heading">
      {fromHistory ? <Link to="/historico">Voltar ao Histórico</Link> : null}
      <h2 id="match-heading">Partida {matchId}</h2>

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
        <>
          <p className="list__meta">
            {isDemoMatchId(match.matchId) ? <span className="tag">Demonstração</span> : null} Patch{" "}
            {match.patch} · fila {match.queue} · mapa {match.map} · {formatDate(match.startAt)}
          </p>
          {isDemoMatchId(match.matchId) ? (
            <p className="muted">
              Partida sintética: valida o fluxo do sistema e não representa um jogo real.
            </p>
          ) : null}

          <PreparationPanel
            preparation={match.preparation}
            busy={preparing}
            onPrepare={() => void handlePrepare()}
            requestError={prepareError}
          />

          {match.preparation.status === "READY" ? (
            <>
              <Participants match={match} selected={participantId} fromHistory={fromHistory} />
              {participantId === null ? (
                <p className="muted">Selecione um participante para listar as decisões dele.</p>
              ) : selectedParticipant === null ? (
                <div className="notice notice--info" role="note">
                  <p>O participante {participantId} não existe nesta partida.</p>
                </div>
              ) : (
                <DecisionList matchId={match.matchId} participantId={participantId} />
              )}
            </>
          ) : null}
        </>
      ) : null}

      {!fromHistory ? <p><Link to="/">Voltar ao início</Link></p> : null}
    </section>
  );
}
