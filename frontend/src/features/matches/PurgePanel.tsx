import { useEffect, useRef, useState } from "react";
import { api, type PurgePreview } from "../../shared/api/client";
import { ApiError, isAbortError } from "../../shared/api/errors";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { Link } from "../../shared/router/Link";

/**
 * Remover a partida e tudo que deriva dela (CC-603, UC05). A prévia mostra o que sai e o
 * que é invalidado; a remoção só é enviada depois de a pessoa digitar o id da partida, e o
 * acompanhamento usa o job PURGE em /imports/{jobId}.
 */

const COUNT_TEXT: ReadonlyArray<[keyof PurgePreview["counts"], string, string]> = [
  ["participants", "participante", "participantes"],
  ["timelineEvents", "evento da linha do tempo", "eventos da linha do tempo"],
  ["decisionStates", "estado antes de compra", "estados antes das compras"],
  ["decisionPoints", "ponto de decisão", "pontos de decisão"],
  ["analyses", "análise", "análises"],
  ["learningAttempts", "tentativa de estudo (com reflexão)", "tentativas de estudo (com reflexões)"],
  ["feedback", "julgamento de revisor", "julgamentos de revisor"],
];
const POLL_MS = 1000;
const POLL_LIMIT = 60;

type Phase = "closed" | "loading" | "ready" | "removing" | "done";

export function PurgePanel({ matchId }: { matchId: string }) {
  const [phase, setPhase] = useState<Phase>("closed");
  const [preview, setPreview] = useState<PurgePreview | null>(null);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<unknown>(null);
  const controller = useRef<AbortController | null>(null);
  const doneNotice = useRef<HTMLDivElement>(null);

  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    if (phase === "done") {
      doneNotice.current?.focus();
    }
  }, [phase]);

  async function open() {
    setPhase("loading");
    setError(null);
    controller.current?.abort();
    const own = new AbortController();
    controller.current = own;
    try {
      setPreview(await api.getPurgePreview(matchId, own.signal));
      setPhase("ready");
    } catch (caught) {
      if (!isAbortError(caught)) {
        setError(caught);
        setPhase("closed");
      }
    }
  }

  async function remove() {
    setPhase("removing");
    setError(null);
    const own = new AbortController();
    controller.current = own;
    try {
      const accepted = await api.purgeMatch(matchId, own.signal);
      for (let attempt = 0; attempt < POLL_LIMIT; attempt += 1) {
        const status = await api.getImport(accepted.jobId, own.signal);
        if (status.status === "SUCCEEDED") {
          setPhase("done");
          return;
        }
        if (status.status === "FAILED") {
          setError(new ApiError(500, status.errorCode ?? "PURGE_FAILED"));
          setPhase("ready");
          return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, POLL_MS));
      }
      setError(new ApiError(0, "PURGE_FAILED"));
      setPhase("ready");
    } catch (caught) {
      if (!isAbortError(caught)) {
        setError(caught);
        setPhase("ready");
      }
    }
  }

  if (phase === "done") {
    return (
      <div className="notice notice--info" role="status" tabIndex={-1} ref={doneNotice}>
        <p>
          Partida removida. Os dados derivados foram apagados, e modelos treinados com ela saíram de
          ativo.
        </p>
        <Link to="/historico">Voltar ao Histórico</Link>
      </div>
    );
  }

  return (
    <section className="section" aria-labelledby="purge-heading">
      <h3 id="purge-heading">Remover esta partida</h3>
      <p className="muted">
        Apaga a partida deste computador, com tudo que deriva dela: estados, decisões, análises,
        tentativas, reflexões, julgamentos e arquivos brutos.
      </p>
      {phase === "closed" ? (
        <button type="button" className="button button--secondary" onClick={() => void open()}>
          Ver o que será removido
        </button>
      ) : null}
      {phase === "loading" ? <p className="muted">Carregando a prévia…</p> : null}
      {preview && (phase === "ready" || phase === "removing") ? (
        <>
          <ul>
            {COUNT_TEXT.map(([key, one, many]) => (
              <li key={key}>
                {preview.counts[key]} {preview.counts[key] === 1 ? one : many}
              </li>
            ))}
          </ul>
          {preview.datasets.length > 0 ? (
            <p>
              {preview.datasets.length === 1
                ? "1 dataset registrado lista esta partida e será invalidado"
                : `${preview.datasets.length} datasets registrados listam esta partida e serão invalidados`}
              ;{" "}
              {preview.models.length === 1
                ? "1 versão de modelo treinada com ele sai de ativo e não poderá ser promovida de novo."
                : `${preview.models.length} versões de modelo treinadas com eles saem de ativo e não poderão ser promovidas de novo.`}
            </p>
          ) : (
            <p className="muted">Nenhum dataset registrado lista esta partida.</p>
          )}
          {preview.unverifiableDatasets > 0 ? (
            <p className="muted">
              {preview.unverifiableDatasets === 1
                ? "1 dataset sem lista de partidas não pôde ser verificado."
                : `${preview.unverifiableDatasets} datasets sem lista de partidas não puderam ser verificados.`}
            </p>
          ) : null}
          <ul className="muted">
            {preview.limitations.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
          <div className="field">
            <label htmlFor="purge-confirm">Digite o id da partida ({matchId}) para confirmar</label>
            <input
              id="purge-confirm"
              className="form__input"
              autoComplete="off"
              spellCheck={false}
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              disabled={phase === "removing"}
            />
          </div>
          <div className="actions">
            <button
              type="button"
              className="button"
              disabled={typed.trim() !== matchId || phase === "removing"}
              onClick={() => void remove()}
            >
              {phase === "removing" ? "Removendo…" : "Remover definitivamente"}
            </button>
          </div>
        </>
      ) : null}
      {error ? <ErrorNotice error={error} /> : null}
    </section>
  );
}
