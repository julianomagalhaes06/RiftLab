import { useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { DemoNoticeContext } from "./demoNotice";

export function DemoNoticeProvider({ children }: { children: ReactNode }) {
  const [keys, setKeys] = useState<ReadonlySet<string>>(new Set());
  const set = useCallback((key: string, on: boolean) => {
    setKeys((current) => {
      if (current.has(key) === on) return current;
      const next = new Set(current);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });
  }, []);
  const value = useMemo(() => ({ visible: keys.size > 0, set }), [keys, set]);
  return <DemoNoticeContext.Provider value={value}>{children}</DemoNoticeContext.Provider>;
}

export function DemoNotice() {
  const { visible } = useContext(DemoNoticeContext);
  if (!visible) return null;
  return (
    <div className="app__source-banner" role="note">
      Partidas DEMO são sintéticas e identificadas: não vêm da Riot nem representam jogadores reais.
    </div>
  );
}
