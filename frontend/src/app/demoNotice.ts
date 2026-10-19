import { createContext, useContext, useEffect, useId } from "react";

/**
 * CC-903: a faixa "Partidas DEMO são sintéticas" só aparece quando a tela mostra dados
 * de demonstração. Cada tela declara isso com `useDemoNotice(condição)`; a faixa some
 * quando nenhuma tela montada declara.
 */
export type DemoNoticeRegistry = { visible: boolean; set: (key: string, on: boolean) => void };

export const DemoNoticeContext = createContext<DemoNoticeRegistry>({ visible: false, set: () => undefined });

export function useDemoNotice(on: boolean): void {
  const key = useId();
  const { set } = useContext(DemoNoticeContext);
  useEffect(() => {
    set(key, on);
    return () => set(key, false);
  }, [key, on, set]);
}
