import { useCallback, useEffect, useRef } from "react";

/**
 * Cria AbortControllers ligados ao ciclo de vida do componente: todos são
 * cancelados ao desmontar (requisições disparadas por eventos, como enviar um
 * formulário, não sobrevivem à saída da tela).
 */
export function useAbortScope(): () => AbortController {
  const controllers = useRef(new Set<AbortController>());
  useEffect(() => {
    const active = controllers.current;
    return () => {
      for (const controller of active) {
        controller.abort();
      }
      active.clear();
    };
  }, []);
  return useCallback(() => {
    const controller = new AbortController();
    controllers.current.add(controller);
    return controller;
  }, []);
}
