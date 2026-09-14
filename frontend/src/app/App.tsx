import { useEffect, useState } from "react";
import { fetchHealthState, type HealthState } from "../api/health";
import "./App.css";

const STATUS_LABEL: Record<HealthState["kind"], string> = {
  loading: "Verificando disponibilidade...",
  available: "Serviço disponível",
  degraded: "Serviço com suporte limitado",
  unavailable: "Serviço indisponível",
};

function StatusBanner({ state }: { state: HealthState }) {
  const detail = state.kind === "degraded" || state.kind === "unavailable" ? state.detail : null;
  return (
    <div className={`status-banner status-banner--${state.kind}`} role="status" aria-live="polite">
      <span className="status-banner__label">{STATUS_LABEL[state.kind]}</span>
      {detail ? <span className="status-banner__detail">{detail}</span> : null}
    </div>
  );
}

export default function App() {
  const [healthState, setHealthState] = useState<HealthState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetchHealthState().then((state) => {
      if (!cancelled) {
        setHealthState(state);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="app">
      <header>
        <h1>RiftLab</h1>
        <p className="app__subtitle">
          Aprendizagem estratégica por revisão pós-partida de decisões de compra em
          League of Legends.
        </p>
      </header>

      <StatusBanner state={healthState} />

      <section className="app__notice">
        <p>
          Ambiente em modo demonstração: nenhuma partida real foi importada ainda.
          Importação, revisão de compras e o modo Estudo chegam nas próximas
          entregas.
        </p>
      </section>
    </main>
  );
}
