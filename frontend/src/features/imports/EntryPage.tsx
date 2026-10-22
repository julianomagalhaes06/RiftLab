import { useId, useState, type FormEvent } from "react";
import { api, type ImportMode } from "../../shared/api/client";
import { isAbortError } from "../../shared/api/errors";
import { useAbortScope } from "../../shared/hooks/useAbortScope";
import { useRouter } from "../../shared/router/context";
import { isRealMatchId } from "../../shared/router/routes";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { Link } from "../../shared/router/Link";
import { HealthStatus } from "../../app/HealthStatus";
import { useDemoNotice } from "../../app/demoNotice";

/** Partida sintética usada pela demonstração (fixture do backend). */
export const DEMO_MATCH_ID = "DEMO-0008";
const MATCH_ID_ERROR =
  "ID inválido. Use o formato BR1_ seguido de números, por exemplo BR1_1234567890.";

/** Importar uma partida concluída pelo ID ou abrir a demonstração (as importadas ficam no Histórico). */
export function EntryPage() {
  const { navigate } = useRouter();
  const newController = useAbortScope();
  const inputId = useId();
  const errorId = useId();
  const [matchId, setMatchId] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState<ImportMode | null>(null);
  useDemoNotice(true);

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
    <div className="area-page import-page">
      <header className="page-head">
        <p className="page-head__context">Importar partida</p>
        <h2>Trazer uma partida para o RiftLab</h2>
        <p className="page-head__lead">
          Suas partidas ranqueadas recentes chegam sozinhas ao abrir o{" "}
          <Link to="/historico">Histórico</Link>. Aqui você importa outra partida concluída pelo ID ou
          percorre a demonstração.
        </p>
      </header>
      <HealthStatus />
      <div className="import-grid">
        <section className="surface surface--pad" aria-labelledby="analyze-heading">
          <h3 id="analyze-heading">Analisar uma partida</h3>
          <p>Analise partidas concluídas. O RiftLab não acompanha jogos em andamento.</p>
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

        <section className="surface surface--pad" aria-labelledby="demo-heading">
          <h3 id="demo-heading">Explorar demonstração</h3>
          <p>
            Percorra o fluxo completo com uma partida sintética, sem usar a Riot nem chave de API.
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
      </div>

      {submitError ? <ErrorNotice error={submitError} title="Não foi possível iniciar a importação" /> : null}

      <p className="import-page__prototype muted">
        Protótipo navegável do CC-102, com dados fictícios e sem relação com a API:{" "}
        <button type="button" className="button button--link" onClick={() => navigate("/prototipo")}>
          Abrir protótipo (dados ilustrativos)
        </button>
      </p>
    </div>
  );
}
