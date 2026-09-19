import { useCallback, useEffect, useState } from "react";
import { api, type DecisionState } from "../../shared/api/client";
import { isAbortError } from "../../shared/api/errors";

type DecisionLoad =
  | { decisionId: string; status: "loading" }
  | { decisionId: string; status: "ready"; decision: DecisionState }
  | { decisionId: string; status: "error"; error: unknown };

/** Carrega uma decisão (GET /decisions/:id), cancelável ao sair da tela ou trocar o id. */
export function useDecision(decisionId: string) {
  const [state, setState] = useState<DecisionLoad>({ decisionId, status: "loading" });
  const [reloads, setReloads] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api
      .getDecision(decisionId, controller.signal)
      .then((decision) => setState({ decisionId, status: "ready", decision }))
      .catch((error: unknown) => {
        if (!isAbortError(error)) {
          setState({ decisionId, status: "error", error });
        }
      });
    return () => controller.abort();
  }, [decisionId, reloads]);

  const reload = useCallback(() => {
    setState({ decisionId, status: "loading" });
    setReloads((count) => count + 1);
  }, [decisionId]);

  const current: DecisionLoad =
    state.decisionId === decisionId ? state : { decisionId, status: "loading" };
  return { ...current, reload };
}
