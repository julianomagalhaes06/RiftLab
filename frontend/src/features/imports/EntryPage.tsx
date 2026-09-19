import { useId, useState, type FormEvent } from "react";
import { api, type ImportMode } from "../../shared/api/client";
import { isAbortError } from "../../shared/api/errors";
import { useAbortScope } from "../../shared/hooks/useAbortScope";
import { useRouter } from "../../shared/router/context";
import { isRealMatchId } from "../../shared/router/routes";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { MatchList } from "./MatchList";

/** Partida sintética usada pela demonstração (fixture do backend). */
export const DEMO_MATCH_ID = "DEMO-0008";
const MATCH_ID_ERROR =
  "ID inválido. Use o formato BR1_ seguido de números, por exemplo BR1_1234567890.";

/** Entrada: importar uma partida concluída, explorar a demonstração ou retomar uma já importada. */
export function EntryPage() {
  const { navigate } = useRouter();
  const newController = useAbortScope();
  const inputId = useId();
  const errorId = useId();
  const [matchId, setMatchId] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState<ImportMode | null>(null);

  async function startImport(id: string, mode: ImportMode) {
    setSubmitError(null);
    setSubmitting(mode);
    const controller = newController();
    try {
      const accepted = await api.createImport(id, mode, controller.signal);
      navigate({ name: "import", jobId: accepted.jobId });
    } catch (error) {
      if (!isAbortError(error)) {
        setSubmitError(error);
        setSubmitting(null);
      }
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const value = matchId.trim();
    if (!isRealMatchId(value)) {
      setFieldError(MATCH_ID_ERROR);
      return;
    }
    setFieldError(null);
    void startImport(value, "RIOT");
  }

  return (
    <>
      <section className="section" aria-labelledby="analyze-heading">
        <h2 id="analyze-heading">Analisar uma partida</h2>
        <p className="notice notice--info" role="note">
          Analise partidas concluídas. O RiftLab não acompanha jogos em andamento.
        </p>
        <form onSubmit={handleSubmit} noValidate className="form">
          <label htmlFor={inputId} className="form__label">
            ID da partida (formato BR1_número)
          </label>
          <input
            id={inputId}
            name="matchId"
            className="form__input"
            type="text"
            inputMode="text"
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            placeholder="BR1_1234567890"
            value={matchId}
            onChange={(event) => setMatchId(event.target.value)}
            aria-invalid={fieldError !== null}
            aria-describedby={fieldError ? errorId : undefined}
          />
          {fieldError ? (
            <p id={errorId} className="form__error" role="alert">
              {fieldError}
            </p>
          ) : null}
          <button type="submit" className="button" disabled={submitting !== null}>
            {submitting === "RIOT" ? "Enviando…" : "Analisar partida"}
          </button>
        </form>
      </section>

      <section className="section" aria-labelledby="demo-heading">
        <h2 id="demo-heading">Explorar demonstração</h2>
        <p className="muted">
          No modo demonstração você percorre o fluxo completo com uma partida sintética,
          sem usar a Riot nem chave de API.
        </p>
        <button
          type="button"
          className="button button--secondary"
          onClick={() => void startImport(DEMO_MATCH_ID, "DEMO")}
          disabled={submitting !== null}
        >
          {submitting === "DEMO" ? "Enviando…" : "Explorar demonstração"}
        </button>
      </section>

      {submitError ? <ErrorNotice error={submitError} title="Não foi possível iniciar a importação" /> : null}

      <MatchList />

      <section className="section section--secondary" aria-labelledby="prototype-heading">
        <h2 id="prototype-heading">Protótipo de jornadas</h2>
        <p className="muted">
          Protótipo navegável do CC-102 com dados fictícios, sem relação com partidas reais nem
          com a API.
        </p>
        <button type="button" className="button button--link" onClick={() => navigate("/prototipo")}>
          Abrir protótipo (dados ilustrativos)
        </button>
      </section>
    </>
  );
}
