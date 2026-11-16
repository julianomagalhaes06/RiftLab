import { useEffect, useState } from "react";

/** Espera que justifica esmaecer o conteúdo anterior; resposta mais rápida troca direto. */
export const SLOW_WAIT_MS = 250;

/**
 * Verdadeiro só quando a espera por `key` passa de `afterMs`. Telas que mantêm o conteúdo anterior
 * enquanto o novo carrega usam isto para esmaecê-lo só numa espera longa: resposta rápida troca
 * direto, sem animar o texto (animar a cada troca fazia a tela piscar). Cada espera começa do zero,
 * mesmo voltando a uma chave que já demorou antes.
 */
export function useSlowWait(waiting: boolean, key: string, afterMs = SLOW_WAIT_MS): boolean {
  const [slowFor, setSlowFor] = useState<string | null>(null);
  useEffect(() => {
    if (!waiting) return undefined;
    const timer = window.setTimeout(() => setSlowFor(key), afterMs);
    return () => {
      window.clearTimeout(timer);
      setSlowFor(null);
    };
  }, [waiting, key, afterMs]);
  return waiting && slowFor === key;
}
