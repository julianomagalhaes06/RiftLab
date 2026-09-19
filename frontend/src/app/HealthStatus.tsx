import { useEffect, useState } from "react";
import { fetchHealthState, type HealthState } from "../api/health";

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

/** Disponibilidade da API (/health/live e /health/ready), mostrada na entrada. */
export function HealthStatus() {
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

  return <StatusBanner state={healthState} />;
}
