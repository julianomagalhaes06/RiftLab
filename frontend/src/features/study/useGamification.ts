import { useEffect, useState } from "react";
import { api, STUDY_EVENT, type LearningGamification } from "../../shared/api/client";

/**
 * Gamificação do estudo (11/11/2026): XP e nível, sequência diária, conquistas e missões da
 * semana, calculados pelo servidor das tentativas reais. Mede quanto a pessoa estudou, não
 * quanto ela joga bem: nada aqui é nota nem estimativa de habilidade.
 */
export function useGamification(matchId?: string | null): LearningGamification | null {
  // Recarrega quando a pessoa responde, revela ou reflete (evento do cliente da API).
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const bump = () => setVersion((value) => value + 1);
    window.addEventListener(STUDY_EVENT, bump);
    return () => window.removeEventListener(STUDY_EVENT, bump);
  }, []);
  const scope = matchId ?? "";
  const [state, setState] = useState<{ scope: string; data: LearningGamification } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api.getGamification(scope || null, controller.signal)
      .then((data) => { if (!controller.signal.aborted) setState({ scope, data }); })
      .catch(() => undefined);
    return () => controller.abort();
  }, [scope, version]);
  // Ao recarregar depois de um estudo, mantém o valor anterior até o novo chegar (sem piscar).
  return state?.scope === scope ? state.data : null;
}

