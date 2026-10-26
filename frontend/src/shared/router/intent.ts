import { useCallback, useState } from "react";

/**
 * Intenção do operador (CC-1218). Toda entrada criada pelo roteador do RiftLab leva esta marca no
 * `history.state`; um documento aberto por link, favorito ou navegação de outro site começa com
 * `history.state` nulo. O que gasta a chave da Riot ao abrir uma página (perfil e partida ao vivo
 * de um Riot ID que veio da URL) só roda sozinho com a marca; sem ela, espera um clique.
 * Recarregar a página mantém o `history.state`, então quem já confirmou não confirma de novo.
 */
export const IN_APP_STATE = { riftlab: true } as const;

export function openedInApp(): boolean {
  const state: unknown = window.history.state;
  return typeof state === "object" && state !== null && (state as { riftlab?: unknown }).riftlab === true;
}

/** `[confirmado, confirmar]`: confirmar marca a entrada atual, como se tivesse vindo do app. */
export function useRiotIntent(): [boolean, () => void] {
  const [confirmed, setConfirmed] = useState(openedInApp);
  const confirm = useCallback(() => {
    window.history.replaceState(IN_APP_STATE, "", window.location.href);
    setConfirmed(true);
  }, []);
  return [confirmed, confirm];
}
